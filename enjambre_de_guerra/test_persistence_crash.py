"""Stress: persistence crash testing.

El test_persistence_endurance.py previo asumía atomicidad sin probarla.
Aquí matamos un subprocess en medio del checkpoint y verificamos que el
disk file siempre está en uno de dos estados:

- El snapshot ANTERIOR (la operación nunca completó), o
- El snapshot NUEVO completo (la operación completó antes del kill).

Nunca un híbrido. Si el archivo final es híbrido, ``decode_blob`` debe
detectar el corruption y fallar con ``MSCSecurityError`` o
``ValueError``, NO retornar data corrupta silenciosamente.

Limitaciones honestas:

- Usamos ``Process.terminate()`` (SIGTERM en POSIX,
  ``TerminateProcess`` en Windows). SIGKILL en POSIX sería más
  agresivo pero ``Process.kill()`` is what multiprocessing exposes
  cross-platform; en POSIX éste es SIGKILL, en Windows es
  ``TerminateProcess``. Cualquiera mata el proceso sin cleanup,
  lo cual es el peor caso para atomicity.
- El test inyecta el kill en un punto fijo (timer-based, no
  signal-based en bytes-written) — un fuzz exhaustivo por
  byte-offset es Phase 7.x followup. Lo que SÍ pruebas: que la
  estrategia ``write + replace`` produce los dos estados válidos
  en ambos extremos (kill antes del replace = snapshot viejo
  intacto; kill después = snapshot nuevo presente).
- En Windows hay una caveat extra: ``os.replace`` no es atomic
  cross-volume y ``WinError`` puede suceder bajo handle race con
  AV scanners. Documentamos pero no probamos esos casos.
"""

from __future__ import annotations

import multiprocessing as _mp
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.stress,
    pytest.mark.posix_only,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="multiprocessing fork + signal-based abort is POSIX-only; "
        "Windows crash testing requires a separate harness via subprocess.Popen "
        "(deferred to Phase 7.x followup)",
    ),
]


def _checkpoint_loop_worker(path_str: str, n_iters: int) -> None:
    """Run inside a forked child. Writes ``n_iters`` checkpoints
    sequentially to ``path``. Designed to be killed mid-write by
    the parent."""
    # Hacemos el setup explícitamente importable post-fork.
    from hoc.core import HoneycombConfig, HoneycombGrid

    path = Path(path_str)
    grid = HoneycombGrid(HoneycombConfig(radius=2))
    for i in range(n_iters):
        # Mark each iteration so a successful read post-kill can
        # tell which checkpoint we're at.
        grid._tick_count = i
        grid.checkpoint(path)


def _kill_after(proc: _mp.Process, secs: float) -> None:
    """Sleep secs, then kill. Used to inject mid-checkpoint death."""
    time.sleep(secs)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=2.0)


class TestPersistenceCrashContract:
    """Hipótesis a probar: ``HoneycombGrid.checkpoint`` es atomic
    bajo crash repentino — el archivo de salida siempre es decodable
    o ausente, nunca un híbrido válido-pero-truncado."""

    def test_kill_mid_write_leaves_snapshot_decodable(self, tmp_path):
        """Forka un worker que escribe 100 checkpoints; mátalo a los
        ~50ms (debería estar en medio de uno). Verifica que el
        archivo final es decodable (snapshot anterior O nuevo)
        o no existe — nunca un híbrido."""
        from hoc.core import HoneycombGrid

        path = tmp_path / "racy.bin"

        ctx = _mp.get_context("fork")
        proc = ctx.Process(
            target=_checkpoint_loop_worker,
            args=(str(path), 1_000),
            daemon=True,
        )
        proc.start()

        # Let it write a few snapshots, then kill in the middle of
        # the next write.
        _kill_after(proc, 0.05)

        # If the file exists, it must decode cleanly. The two
        # possible legitimate post-kill states:
        #
        # 1) The file doesn't exist yet (kill before first write
        #    completed → tmp file orphaned, target absent).
        # 2) The file decodes to a valid grid with some tick_count
        #    in [0, 1000).
        #
        # The state we're asserting against: file exists but is
        # invalid mid-write garbage. That's the contract violation.
        if not path.exists():
            return  # caso 1, OK

        # caso 2: must decode without raising.
        try:
            restored = HoneycombGrid.restore_from_checkpoint(path)
        except Exception as exc:
            pytest.fail(
                f"checkpoint atomicity violated: file exists at {path} but "
                f"failed to decode after mid-write kill — got {type(exc).__name__}: "
                f"{exc}. Either the write+replace dance broke or the worker "
                f"left a half-flushed file."
            )
        assert 0 <= restored._tick_count < 1_000

    def test_no_orphan_tmp_after_clean_completion(self, tmp_path):
        """En el happy path (sin kill), no debe quedar archivo
        ``.tmp`` después del replace."""
        from hoc.core import HoneycombConfig, HoneycombGrid

        path = tmp_path / "clean.bin"
        grid = HoneycombGrid(HoneycombConfig(radius=1))
        grid.checkpoint(path)
        assert path.exists()
        assert not (tmp_path / "clean.bin.tmp").exists()

    def test_repeated_kills_never_corrupt_file(self, tmp_path):
        """5 ciclos de "fork + kill mid-write" sobre el mismo path.
        En cada ciclo, el archivo final debe seguir siendo decodable
        (o ausente). Si alguno corrompe el archivo, fallamos."""
        from hoc.core import HoneycombGrid

        path = tmp_path / "repeated.bin"
        ctx = _mp.get_context("fork")

        for cycle in range(5):
            proc = ctx.Process(
                target=_checkpoint_loop_worker,
                args=(str(path), 500),
                daemon=True,
            )
            proc.start()
            _kill_after(proc, 0.03 + 0.01 * cycle)  # vary kill timing

            if path.exists():
                try:
                    HoneycombGrid.restore_from_checkpoint(path)
                except Exception as exc:
                    pytest.fail(
                        f"cycle {cycle}: file at {path} corrupted after "
                        f"mid-write kill: {type(exc).__name__}: {exc}"
                    )
            # Cleanup any orphan .tmp from the killed worker before
            # next cycle.
            tmp_file = tmp_path / "repeated.bin.tmp"
            if tmp_file.exists():
                # Orphan tmp is OK (kill happened mid-write). Just
                # remove it before next cycle to avoid confusing the
                # next worker.
                tmp_file.unlink()

    def test_concurrent_writers_against_same_file_serialize_via_replace(self, tmp_path):
        """Dos workers escribiendo al mismo path; ninguno deja al
        archivo en estado corrupto, aunque uno gane la carrera del
        replace.

        No es un test de "exclusión mutua entre writers" (ese
        contract no existe — el último writer gana). Es un test de
        "ningún writer deja un híbrido visible aún cuando race".
        """
        from hoc.core import HoneycombGrid

        path = tmp_path / "shared.bin"
        ctx = _mp.get_context("fork")

        p1 = ctx.Process(target=_checkpoint_loop_worker, args=(str(path), 100), daemon=True)
        p2 = ctx.Process(target=_checkpoint_loop_worker, args=(str(path), 100), daemon=True)
        p1.start()
        p2.start()
        # Let them run a bit, then force-stop both.
        time.sleep(0.1)
        for p in (p1, p2):
            if p.is_alive():
                p.kill()
                p.join(timeout=2.0)

        # File must be decodable or absent.
        if path.exists():
            HoneycombGrid.restore_from_checkpoint(path)  # raises if corrupt

        # Cleanup any orphan tmp.
        tmp_file = tmp_path / "shared.bin.tmp"
        if tmp_file.exists():
            tmp_file.unlink()


class TestPersistenceTmpFileHygiene:
    """Tests separados que NO requieren fork, para que corran
    también en Windows. Cubren el lado mecánico del write+replace
    sin probar atomicity-bajo-kill."""

    @pytest.mark.posix_only  # we keep posix_only because of the @kill marker class above; remove later
    def test_explicit_tmp_orphan_does_not_break_next_checkpoint(self, tmp_path):
        """Si por alguna razón quedó un .tmp huérfano de un crash
        previo, el siguiente checkpoint debe poder sobrescribirlo
        cleanly. Validamos que no hay un assert/check defensivo
        que rechace .tmp pre-existentes."""
        from hoc.core import HoneycombConfig, HoneycombGrid

        path = tmp_path / "next.bin"
        tmp_marker = tmp_path / "next.bin.tmp"
        # Create an orphan tmp (simulating a previous crash).
        tmp_marker.write_bytes(b"garbage from previous crash")

        grid = HoneycombGrid(HoneycombConfig(radius=1))
        # Should overwrite the orphan tmp + replace successfully.
        grid.checkpoint(path)
        assert path.exists()
        # The orphan tmp got consumed by the checkpoint (tmp file
        # is reused as the staging area).
        # Either it's gone (overwritten then replaced) or empty —
        # both are fine.
        assert (
            not tmp_marker.exists() or tmp_marker.read_bytes() != b"garbage from previous crash"
        ), (
            "orphan .tmp survived a fresh checkpoint with original content; "
            "checkpoint should have written over it."
        )
