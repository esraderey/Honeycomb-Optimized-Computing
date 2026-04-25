# Changelog

Todas las modificaciones notables del proyecto **HOC (Honeycomb Optimized
Computing)** se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y este proyecto adhiere a [Semantic Versioning](https://semver.org/lang/es/).

---

## [1.4.1-phase04.1] — 2026-04-24

**Cierre de Fase 4.1 — TaskLifecycle wire-up + `choreo` static FSM checker.**
705 tests pasando (+42 vs Phase 4: 10 wire-up + 32 choreo). Una segunda
FSM declarativa de Phase 4 (`TaskLifecycle`) **graduada a wired** vía
`HiveTask.__setattr__`: cada `task.state = X` ahora valida la transición
contra el FSM y rechaza estados ilegales con `IllegalStateTransition`.
Nueva herramienta `choreo` (subpaquete propio en `choreo/`, ~600 LOC)
realiza verificación estática AST-based sobre el repo: detecta
mutaciones undocumented, dead states, enum-extra states, FSMs declarative-
only. Aplicada a HOC produce el reporte exacto esperado: 0 errores,
2 warnings (B12-bis, B12-ter), 3 info. Nuevo job CI `choreo-static-check`
en `lint.yml`. Sin nuevas dependencias runtime. Bandit/pip-audit/ruff/
black/mypy limpio.

Reporte completo: [snapshot/PHASE_04_1_CLOSURE.md](snapshot/PHASE_04_1_CLOSURE.md).

### Added

#### `choreo/` — static FSM verification (new subpackage, MIT)
- `choreo/walker.py` — `ast.NodeVisitor` que captura tres patrones:
  `obj.state = ENUM.MEMBER`, `obj._set_state(ENUM.MEMBER)`, y
  `class X(Enum)` con sus members.
- `choreo/spec.py` — importa `state_machines/*_fsm.py`, llama
  `build_<stem>()`, extrae estados + transiciones del `HocStateMachine`.
- `choreo/diff.py` — bind FSM↔Enum por subset de members; produce
  findings con severidades error/warning/info.
- `choreo/cli.py` — entry point `python -m choreo check` con
  `--json`, `--strict`, `--root <path>`, `--specs-dir <name>`.
- `choreo/types.py` — frozen dataclasses (Mutation, EnumDecl,
  FsmSpec, Finding) determinísticos para comparación + serialización.

#### Wire-up TaskLifecycle (Phase 4 declarativa → wired)
- `HiveTask.__post_init__` instancia un `_fsm = build_task_fsm()`
  por tarea.
- `HiveTask.__setattr__` rutea cada `task.state = X` a
  `_fsm.transition_to(X.name)`. Levanta `IllegalStateTransition` en
  edges no declaradas (e.g. `COMPLETED → RUNNING`, `RUNNING → PENDING`
  sin retry).
- Dos transiciones explícitas (NO wildcards) añadidas a
  `state_machines/task_fsm.py` para los 5 test-sites de
  `tests/test_swarm.py` que fuerzan estados terminales sobre tareas
  PENDING (`force_completed_from_pending`,
  `force_failed_from_pending`).
- Sync vía `_fsm.reset(state.name)` cuando el caller pasa un state
  no-default por `__init__`.

#### CI
- Nuevo job **`choreo-static-check`** en `.github/workflows/lint.yml`
  corre `python -m choreo check` y `python -m choreo check --json` para
  validar JSON shape.

#### Documentation
- **ADR-008** — `choreo`, static FSM verification complementary to
  runtime wire-up.

#### `state_machines/`
- Nueva property `HocStateMachine.transitions` retorna lista de
  edges `(source, dest, trigger)`. Usada por `choreo/spec.py` para
  evitar acceso a `_dest_index` privado.
- Docstring de `task_fsm.py` actualizada — ya no es declarativa-only.

#### Tests
- `tests/test_choreo.py` — 32 tests (walker, spec, diff, CLI, HOC
  integration smoke).
- `tests/test_state_machines.py::TestTaskFSMWired` — 10 tests del
  wire-up (legal/ilegal/idempotente/ASSIGNED dead/test-fixture
  edges/sync).

### Detected (deferred to Phase 5+)

choreo confirmó al correrse contra HOC los dos bugs latentes
documentados en Phase 4:

- **B12-bis** — `TaskState.ASSIGNED` declarado en `swarm.py:90` pero
  nunca asignado (warning `enum_extra_state`).
- **B12-ter** — `CellState.{SPAWNING, MIGRATING, SEALED, OVERLOADED}`
  declarados en `core/cells_base.py:51` pero nunca asignados (warning
  `dead_state`).

Tras resolución de ambos en Phase 5+, el job CI puede flippear a
`--strict` para hacer fail también con warnings.

---

## [1.4.0-phase04] — 2026-04-24

**Cierre de Fase 4 — Configuración & Developer Experience (FSM integration).**
663 tests pasando (+81: 57 unit + 16 hypothesis property + 8 mermaid
export), cobertura global **76.34 %** (+0.61 pts vs Phase 3). Cinco state
machines formales para HOC (`CellState`, `PheromoneDeposit`,
`TaskLifecycle`, `QueenSuccession`, `FailoverFlow`), una **wired into
production** (`HoneycombCell.state.setter`) y cuatro declarativas-only
(documentación + Mermaid + property tests). `swarm.py` y `nectar.py`
**graduados** del override mypy de Phase 3 (29 anotaciones inline). Bug
latente **B12** descubierto y corregido (`RoyalJelly.get_stats` referenciaba
atributo inexistente en enum `RoyalCommand`). Bandit sigue 0/0/0; pip-audit
limpio.

Reporte completo: [snapshot/PHASE_04_CLOSURE.md](snapshot/PHASE_04_CLOSURE.md).

### Added

#### Runtime dependency
- `tramoya==1.4.0` pinneado en `requirements.txt` y declarado en
  `pyproject.toml [project].dependencies`. Provee el motor de state
  machines (~300 LOC, zero deps, MIT). Aislado tras
  `hoc.state_machines.HocStateMachine` — única importación de tramoya
  en todo el repo, mismo patrón que Phase 2 con `mscs` en `hoc.security`.
  Ver [ADR-007](docs/adr/ADR-007-tramoya-fsm-integration.md).

#### `hoc.state_machines` subpaquete
- **`base.py`** — `HocStateMachine`, `HocTransition`,
  `IllegalStateTransition`. API destination-driven (`transition_to(target)`)
  preserva el contrato pre-Phase-4 de `obj.state = X`; trigger-driven
  (`trigger(name)`) está disponible para callers que prefieren eventos.
- **`cell_fsm.py`** (wired) — 9 estados (mismos que `CellState` enum),
  14 transiciones (9 lifecycle + 5 admin/wildcard).
- **`pheromone_fsm.py`** (declarativo) — 4 estados
  (FRESH/DECAYING/DIFFUSING/EVAPORATED), 5 transiciones con guards.
- **`task_fsm.py`** (declarativo) — 5 estados (PENDING/RUNNING/
  COMPLETED/FAILED/CANCELLED), 6 transiciones. `ASSIGNED` declarado en
  `TaskState` enum pero **nunca asignado** (B12-bis, deferred).
- **`succession_fsm.py`** (declarativo) — 6 estados (STABLE/DETECTING/
  NOMINATING/VOTING/ELECTED/FAILED), 9 transiciones modelando heartbeat-
  loss → confirm → nominate → vote → elect/fail → cooldown. Guards
  re-statement de los chequeos `_tally_votes` (quorum + signatures + term).
- **`failover_fsm.py`** (declarativo) — 5 estados (HEALTHY/DEGRADED/
  MIGRATING/RECOVERED/LOST), 6 transiciones; undo en MIGRATING modela
  el rollback de migración.

#### CellState FSM wired
- `core/cells_base.py:HoneycombCell` ahora instancia un `HocStateMachine`
  por celda en `__init__`. `state.setter` y `_set_state` enrutan toda
  transición por la FSM antes de mutar `_state`. Transiciones a estados
  muertos (`SPAWNING`, `MIGRATING`, `SEALED`, `OVERLOADED` — B12-ter,
  deferred) levantan `IllegalStateTransition(reason="no_edge")`.
- Idempotencia preservada: `cell.state = current_state` sigue siendo
  no-op (sin invocar la FSM).

#### Documentación
- `docs/state-machines.md` — auto-generado por
  `scripts/generate_state_machines_md.py`. Contiene índice + 5
  diagramas Mermaid `stateDiagram-v2`, output determinista byte-a-byte.
- [ADR-007](docs/adr/ADR-007-tramoya-fsm-integration.md) — rationale de
  la integración tramoya, la decisión "1 wired + 4 declarative", y el
  hack del exclude+explicit-package-bases para mypy.
- [ADR-006](docs/adr/ADR-006-mypy-legacy-suppression.md) actualizado con
  el outcome de la graduación Phase 4 (`swarm.py` + `nectar.py` removidos
  del override).

#### Tests
- `tests/test_state_machines.py` (57 tests) — wrapper API + per-FSM
  legal/illegal transitions + CellState wiring smoke (transiciones
  ilegales rechazadas sin mutar la celda).
- `tests/test_state_machines_property.py` (16 tests, Hypothesis) —
  reachability random walks, terminal-state invariantes, no-orphan-states.
- `tests/test_mermaid_export.py` (8 tests) — determinism, FSM coverage,
  drift detector contra `docs/state-machines.md` (mismo contrato que
  el `--check` de CI).

#### CI
- `.github/workflows/lint.yml` `mypy` job extendido con step
  `python -m mypy --explicit-package-bases state_machines/*.py` (strict
  preservado pese al exclude global del directorio).
- Nuevo job **`state-machines-doc`** corre
  `python scripts/generate_state_machines_md.py --check` — falla si la
  doc auto-generada drifteó de las specs FSM.

### Changed

- `pyproject.toml [tool.mypy].exclude` += `^state_machines/` con
  comentario explicando el conflict cwd-name vs. sys.path-search y la
  invocación correcta para CI/local.
- `pyproject.toml [[tool.mypy.overrides]]` para módulos legacy: `nectar`
  y `swarm` **removidos** (graduación ADR-006).
- `pyproject.toml [tool.setuptools].packages` += `hoc.state_machines`.
- `__init__.py` no tocado — `from hoc import ...` sigue dando exactamente
  los mismos símbolos. (Mantenido el invariante cardinal de Phase 3.)

### Fixed

#### B12 — `RoyalJelly.get_stats` AttributeError latente
- `nectar.py:~1174` referenciaba `cmd.command` sobre miembros del enum
  `RoyalCommand`. Llamadas a `RoyalJelly.get_stats()` habrían arrojado
  `AttributeError` en runtime (ningún test lo cubría). Mismo patrón que
  **B9** (Phase 1 metrics.py) y **B11** (Phase 3 resilience.py): mypy
  strict captura el lookup.
- **Fix**: `cmd.command.name` → `cmd.name`; `c.command == cmd.command`
  → `c.command == cmd`. Comportamiento original preservado.

### Annotated (29 errores mypy → 0)

#### `swarm.py` (11 errores)
- `HiveTask.__post_init__` retorna `-> None`.
- `LoadDistribution.__init__` retorna `-> None`.
- `pheromone_score: float = 0.0` (era inferido `int`, asignado `float`).
- `_explore_area` returna `dict[str, Any]` con anotación explícita del
  literal.
- `ring_counts: defaultdict[int, int]`, `behavior_counts:
  defaultdict[str, int]`, `suggestions: list[tuple[HexCoord, HexCoord, int]]`,
  `best_load: float = 0.0`.
- `submit_task(callback: Callable[[Any], None] | None)` (era bare
  `Callable`).

#### `nectar.py` (18 errores)
- `_canonical_payload` (3 ocurrencias en PheromoneDeposit, DanceMessage,
  RoyalMessage): `cast(bytes, _mscs.dumps(...))`.
- `dict | None` parámetros (6 ocurrencias) widened a `dict[str, Any] | None`.
- `defaultdict` generics: `defaultdict[str, float]`, `defaultdict[str, int]`.
- `applicable: list[RoyalMessage]`, `_queue: deque[Any]`.
- `deposit_pheromone`/`start_dance`: `**kwargs: Any`.
- `new_deposits` inner tuple: `dict[str, Any] | None`.
- B12 fix (ver arriba).

### Deferred to Phase 5+

- **4.8 Config system** — `from_yaml/from_env/from_toml` (priorización
  del usuario; ningún path crítico lo necesita en Phase 4).
- **4.9 CLI `hoc-cli`** — `grid/state-machines/doctor` subcommands
  (priorización del usuario).
- **4.11 Split swarm/nectar** — la graduación mypy ya hizo el trabajo
  difícil; el split puro queda más natural cuando `resilience.py` también
  entra a Phase 5.
- **Wire-up real de las 4 FSMs declarativas** — junto con observability
  (Phase 5) o split de resilience (Phase 5).
- **B12-bis y B12-ter (dead states)** — `TaskState.ASSIGNED` y
  `CellState.{SPAWNING,MIGRATING,SEALED,OVERLOADED}` nunca asignados;
  decisión (eliminar vs. wire-up del callsite faltante) en Phase 5.
- **Cobertura objetivo 78%** — alcanzamos 76.34%; el resto requiere
  boost en `nectar.py` (73%) y `bridge.py` (56%).
- **Benchmark baseline reproducible** — Phase 3 difirió bench (Gap 4 de
  Phase 3 closure); Phase 4 captura `snapshot/bench_phase04.json` pero
  no puede medir overhead vs. Phase 3. Phase 5 captura baseline + diff.

### Audits

- ruff: 0 errores (toda la base)
- black: 0 archivos a reformatear
- mypy `python -m mypy .`: 0 errores (security/memory/resilience/swarm/
  nectar/__init__ + bridge/core/metrics suprimidos por ADR-006)
- mypy `python -m mypy --explicit-package-bases state_machines/*.py`: 0
- bandit: **0 / 0 / 0** (HIGH / MEDIUM / LOW), 9,896 LOC scanned, 31 archivos
- pip-audit (runtime + dev): clean
- radon CC: 11 funciones >10 (todas legacy, sin cambios estructurales)
- pytest: **663/663 passing**

[1.4.1-phase04.1]: https://github.com/esraderey/Honeycomb-Optimized-Computing/releases/tag/v1.4.1-phase04.1
[1.4.0-phase04]: https://github.com/esraderey/Honeycomb-Optimized-Computing/releases/tag/v1.4.0-phase04

---

## [1.3.0-phase03] — 2026-04-24

**Cierre de Fase 3 — Tooling, CI/CD & Code Quality.** 582 tests pasando
(+161: 133 refactor-compat + 28 coverage boosters), cobertura global
**75.73%** (primera vez sobre el target 75%), `core.py` (3,615 LOC)
dividido en 14 submódulos y `metrics.py` (1,169 LOC) en 3 submódulos
sin romper un solo test. 4 GitHub Actions workflows, 7 ADRs, 3 documentos
OSS. Bandit sigue 0/0/0; pip-audit limpio. Bug latente **B11** detectado
por mypy strict sobre `resilience.py` y corregido.

Reporte completo: [snapshot/PHASE_03_CLOSURE.md](snapshot/PHASE_03_CLOSURE.md).

### Added

#### Tooling & quality gates
- `pyproject.toml` [tool.ruff/black/mypy/coverage/bandit] configurados.
  Mypy strict en `security.py`, `memory.py`, `resilience.py`; legacy
  suprimido via `exclude` + `[[tool.mypy.overrides]].ignore_errors` (ver
  [ADR-006](docs/adr/ADR-006-mypy-legacy-suppression.md)).
- `requirements-dev.txt` con 12 dev-deps pinneadas.
- `.pre-commit-config.yaml` con 6 repos de hooks (trailing-whitespace,
  end-of-file-fixer, check-yaml/toml/json, check-added-large-files,
  ruff + ruff-format, black, mypy, bandit).

#### CI/CD
- `.github/workflows/test.yml` — matriz `{ubuntu,macos,windows} × {py3.10,3.11,3.12}`, coverage upload a Codecov.
- `.github/workflows/lint.yml` — jobs paralelos: ruff check, black --check, mypy.
- `.github/workflows/security.yml` — bandit (fail en MEDIUM+), pip-audit, safety; cron semanal los lunes 05:00 UTC.
- `.github/workflows/release.yml` — build sdist+wheel + GitHub release en tags `v*.*.*`; PyPI publish stubbed hasta provisionar cuenta.

#### Refactor estructural
- **`core/` subpackage** (14 submódulos, todos < 800 LOC): `grid.py`,
  `grid_geometry.py`, `grid_config.py`, `cells_base.py`, `cells_specialized.py`,
  `_queen.py`, `cells.py` (facade), `events.py`, `health.py`, `locking.py`,
  `pheromone.py` (internos), `constants.py`, `__init__.py` (con PEP 562
  `__getattr__` para transicionales), y `_metrics_internal.py` (eliminado
  tras mover contenido a `metrics/collection.py`).
- **`metrics/` subpackage**: `collection.py` (primitives + HiveMetrics +
  transicionales movidos desde core), `visualization.py` (HoneycombVisualizer),
  `rendering.py` (HeatmapRenderer, FlowVisualizer), `__init__.py`.
- `core.py` y `metrics.py` **eliminados**; facades preservan 100% del
  API público anterior.

#### Tests
- `tests/test_refactor_compat.py` — 133 tests: re-export parity (67 + 37 + 15 parametrized), identity checks (8 clases), distinct-identity de `CellMetrics` (público vs interno), alias `HexRing = HexRegion`, isinstance cross-path.
- `tests/test_events_health.py` — 28 tests: EventBus (rate limit, async, priority, history, singleton), CircuitBreaker (4 state transitions), HealthMonitor, HexRegion/HexPathfinder.

#### Documentación
- `CONTRIBUTING.md` — dev setup, quality checks, PR flow, code style, roadmap discipline.
- `CODE_OF_CONDUCT.md` — Contributor Covenant v2.1 adoptado por referencia.
- `SECURITY.md` — supported versions, private disclosure channels, coordinated-disclosure timeline, Phase 2 threat model, past advisories (B1–B11).
- `docs/adr/` — 6 ADRs numerados + README + template (Michael Nygard format):
  - ADR-001 Hexagonal topology (retroactivo, v1.0.0).
  - ADR-002 `mscs` replaces `pickle` (Phase 2).
  - ADR-003 Shared HMAC key vs per-cell (Phase 2).
  - ADR-004 `OrderedDict` LRU for `PheromoneTrail` (Phase 2).
  - ADR-005 Raft-like signed-vote quorum (Phase 2).
  - ADR-006 Legacy modules suppressed from strict mypy (Phase 3).

#### Audit snapshots
- `snapshot/bandit_phase03.json` — 0 HIGH / 0 MEDIUM / 0 LOW (8,987 LOC scanned).
- `snapshot/pip_audit_phase03.txt` — "No known vulnerabilities found".
- `snapshot/radon_raw_phase03.txt`, `snapshot/radon_cc_phase03.txt` — raw LOC + cyclomatic complexity.

### Fixed

- **B11** [`resilience.py:1138`] `CombRepair._rebuild_cell()` escribía
  `cell._pheromone_level = 0.0`, pero `HoneycombCell` no tiene tal atributo
  (el backing es `_pheromone_field`, una `PheromoneField`). Silenciosamente
  creaba un atributo muerto y dejaba la feromona original intacta tras el
  rebuild. Fix: reemplazar `_pheromone_field` por una nueva `PheromoneField()`.
  Misma familia que B9 de Fase 1 (ambos detectados por el mismo patrón de
  tooling: anotar tipos y correr mypy strict sobre código legacy).

### Changed

- `__init__.py` re-exports ahora importan desde los subpackages `core/` y `metrics/` en lugar de los antiguos monolitos. Identidades preservadas: `hoc.HexCoord is hoc.core.HexCoord is hoc.core.grid_geometry.HexCoord`.
- Formateo global aplicado por `ruff --fix` (1563 autofixes + 18 unsafe-fixes + 11 manual) y `black` (19 archivos reformateados).

### Deferred

- 5 archivos legacy siguen > 800 LOC: `resilience.py` (1,639), `nectar.py`
  (1,366), `swarm.py` (1,132), `memory.py` (940), `bridge.py` (886). Splits
  planificados para fases 4-6 según ADR-006.
- 6 funciones legacy con CC > 10 (todas en `swarm.py` y `core/grid.py`,
  movidas desde el antiguo `core.py` sin reescribir lógica).
- Mypy strict sobre legacy: suprimido en Phase 3; re-habilitación per-módulo
  en fases siguientes.
- Benchmark end-to-end no corrido para Phase 3 (refactor sintáctico — no
  esperamos regresión de perf; si se considera load-bearing se mide en el PR).
- Workflow `docs.yml` (sphinx): diferido a Fase 9 del roadmap.

---

## [1.2.0-phase02] — 2026-04-23

**Cierre de Fase 2 — Seguridad & Hardening.** 421 tests pasando (43 nuevos
dedicados a seguridad), `pickle` erradicado del código de producción y
reemplazado por `mscs` con HMAC-SHA256, mensajes de `NectarFlow`/`RoyalJelly`
firmados, protocolo Raft-like con votos firmados en `QueenSuccession`,
Bandit limpio en todas las severidades, overhead end-to-end +3.5% (<5%).

Reporte completo: [snapshot/PHASE_02_CLOSURE.md](snapshot/PHASE_02_CLOSURE.md).

### Added

- **`security.py`** (nuevo módulo, 83% cobertura) — primitivas centralizadas:
  `serialize`/`deserialize` con HMAC, `sign_payload`/`verify_signature`,
  `secure_random`/`secure_choice`/`secure_shuffle` sobre `secrets.SystemRandom`,
  `safe_join` con `PathTraversalError`, `RateLimiter`/`rate_limit` token
  bucket, `sanitize_error` (respeta `HOC_DEBUG`).
- **`HOC_HMAC_KEY`** (env var) — permite fijar clave HMAC compartida entre
  procesos. En ausencia, cada proceso genera una clave efímera de 32 bytes.
- **`tests/test_security.py`** — 43 tests cubriendo las 5 áreas obligatorias:
  - `TestMscsRejectsMalicious` (5): payloads pickle-RCE rechazados, HMAC
    tamper detection, foreign key rejection, registry strict, CombStorage
    tamper-detection.
  - `TestRoyalCommandQueenOnly` (6): DroneCell bloqueado en `priority=10`,
    Queen aceptada, threshold exacto, `update_queen_coord`, forge detection.
  - `TestQuorumSignedVotes` (7): voter duplicado, voto sin firma, firma
    manipulada, wrong term, candidato desconocido, mayoría, term monotónico.
  - `TestPheromoneBoundedDoS` (4): 10K deposits/misma coord, 10K distintas,
    metadata flood, auto-sign.
  - `TestHoneyArchivePathTraversal` (5): `../`, absoluto, null byte, key
    válido, primitive `safe_join`.
  - Clases transversales: `TestHmacPrimitives`, `TestCsprng`,
    `TestRateLimiter`, `TestDanceSigning`, `TestHiveMemoryIntegration`.
- **`Vote`** dataclass (`resilience.py`) — voto firmado con
  `voter`/`candidate`/`term`/`timestamp`/`signature` para el protocolo
  Raft-like de sucesión.
- **`signature`** campo opcional en `PheromoneDeposit`, `DanceMessage`,
  `RoyalMessage` con métodos `_canonical_payload()`, `sign()`, `verify()`.
- **`issuer`** en `RoyalMessage` + `RoyalJelly.HIGH_PRIORITY_THRESHOLD=8`.
- **`RoyalJelly.update_queen_coord()`** — propaga la sucesión a la capa de
  comunicación.
- **`QueenSuccession.current_term`** property; `_tally_votes` público-ish
  para testing (incluye contadores de rechazo por razón).
- **`SwarmScheduler.execute_on_cell()`** — API pública para ejecución
  directa sobre celda (rate-limited).
- **`SwarmConfig.submit_rate_per_second`/`submit_rate_burst`/
  `execute_rate_per_second`/`execute_rate_burst`** — tunables del rate
  limiter.
- **`PheromoneTrail` params**: `max_coords` (default 10_000), `max_metadata_keys`
  (default 100). LRU evicción sobre coordenadas.
- **`HoneyArchive._validate_key()`** — rechaza claves con traversal, null
  bytes, o paths absolutos.
- **`snapshot/bandit_phase02.json`** — 0 HIGH, 0 MEDIUM, 0 LOW.
- **`snapshot/pip_audit_phase02.txt`** — "No known vulnerabilities found".
- **`snapshot/PHASE_02_CLOSURE.md`** — reporte completo de cierre.

### Changed

- **`memory.py`**: `pickle.dumps/loads` reemplazados por
  `security.serialize/deserialize` con HMAC-SHA256 en `CombStorage.put/get`
  y `HoneyArchive.archive/retrieve`. `PollenCache.put` usa `mscs.dumps`
  sin HMAC para estimación de tamaño. `PollenCache._evict_one` política
  RANDOM usa `secrets.SystemRandom`.
- **`memory.HoneyArchive.__init__`**: `base_path` default ahora
  `tempfile.gettempdir()/hoc-honey` (fix Bandit B108, antes `/tmp/honey`).
  `base_path` se normaliza a absoluto con `Path.resolve()`.
- **`resilience.py`**: `import pickle` + `pickle.dumps` en
  `CombRepair._check_data_integrity` reemplazados por `security.serialize`.
- **`resilience.QueenSuccession`**: `_conduct_election` refactorizado a
  protocolo Raft-like. Usa `_tally_votes` que rechaza voters duplicados,
  firmas inválidas, terms incorrectos, candidatos desconocidos, y exige
  mayoría estricta (>50%).
- **`nectar.PheromoneTrail`**: `_deposits` migrado de `defaultdict(dict)`
  a `OrderedDict` con cap LRU (`max_coords`). Metadata merge acotada por
  `max_metadata_keys`. Cada deposit nuevo se auto-firma.
- **`nectar.WaggleDance`**: `start_dance` auto-firma; `propagate` preserva
  firma original (los campos mutables quality/ttl están fuera del payload
  canónico).
- **`nectar.RoyalJelly.issue_command`**: acepta kwarg `issuer`. Lanza
  `PermissionError` si `priority >= HIGH_PRIORITY_THRESHOLD` y `issuer` no
  es la Queen actual. Todos los comandos firmados.
- **`swarm.BeeBehavior.should_respond`**: `random.random()` →
  `secure_random()`.
- **`swarm.SwarmScheduler._initialize_behaviors`**: `random.shuffle` →
  `secure_shuffle`.
- **`swarm.SwarmScheduler.submit_task`**: rate-limited vía `RateLimiter`.
- **Logs sanitizados** (via `security.sanitize_error`) en 6 sitios:
  `memory.CombStorage.get`, `memory.HoneyArchive.archive/retrieve`,
  `resilience.CellFailover._migrate_work`, `resilience.SwarmRecovery.execute_recovery_plan`,
  `nectar.WaggleDance.start_dance`, `swarm.SwarmScheduler.tick`,
  `bridge` (2 sitios).

### Fixed

- **Bandit B108** (`memory.HoneyArchive`): default `base_path="/tmp/honey"`
  reemplazado por `tempfile.gettempdir()/hoc-honey` para evitar race/symlink
  attacks en POSIX multi-usuario.
- **Defaultdict silencioso** (`nectar.PheromoneTrail._deposits`): migración
  a `OrderedDict` elimina el crecimiento sin cota que permitía DoS por
  flood de coordenadas.

### Removed

- **`import pickle`** eliminado de `memory.py` y `resilience.py` en path
  de producción. `pickle` solo aparece ahora en `tests/test_security.py`
  como input adversarial para verificar que `mscs` lo rechaza.
- **`import random`** eliminado de `memory.py` (era un import inline en
  `_evict_one`) y `swarm.py` / `resilience.py` (no usados tras el
  reemplazo por `secrets.SystemRandom`).

### Security

- **`pickle` → `mscs`** con HMAC-SHA256 y registry strict cierra el vector
  RCE clásico por `__reduce__` malicioso. Un atacante que plantee bytes
  en CombStorage/HoneyArchive/RPC hit MSCSecurityError antes de reconstruir.
- **HMAC-SHA256 sobre mensajes** de `NectarFlow`/`RoyalJelly` atestigua
  origen (un atacante sin la clave no puede forjar mensajes aceptables).
- **Queen-only enforcement** en `RoyalCommand` priority ≥ 8 cierra el
  vector "DroneCell forja EMERGENCY" aun con clave compartida.
- **Votos firmados + `term_number` monotónico** en `QueenSuccession`
  blindan la elección contra replay, votos duplicados y candidatos
  forjados.
- **CSPRNG** (`secrets.SystemRandom`) en decisiones que afectan el
  scheduling/capacity (respuesta a estímulos, shuffle de roles, política
  RANDOM de evicción de cache) impide manipulación predictiva vía seed
  del RNG global.
- **Rate limiting** en APIs públicas (`submit_task`, `execute_on_cell`)
  con default 1000/s burst 2000 y 10_000/s burst 20_000 respectivamente.
- **Path validation** (`safe_join`) en `HoneyArchive` rechaza traversal,
  rutas absolutas y null bytes — defense in depth antes de que el
  checkpoint a disco se active.
- **Bounded growth** en `PheromoneTrail` con LRU cap (default 10K coords,
  100 metadata keys por deposit) mitiga DoS por flood.
- **Log sanitization**: producción oculta detalles de excepción
  (`HOC_DEBUG=0` default); activable con `HOC_DEBUG=1`.

### Deferred

- Logs sanitizados en `core.py` (6 sitios de callbacks internos) — no
  security-sensitive pero convendría pasarlos por `sanitize_error` en una
  fase futura de consistencia.

---

## [1.1.0-phase01] — 2026-04-22

**Cierre de Fase 1 — Estabilización crítica.** 378 tests pasando, cobertura
83–95% en los 4 módulos previamente sin tests, 0 vulnerabilidades de
dependencias, 0 hallazgos `bandit` HIGH.

Reporte completo: [snapshot/PHASE_01_CLOSURE.md](snapshot/PHASE_01_CLOSURE.md).

### Fixed

#### Bugs del roadmap (B1–B8)
- **B1** [`core.py`] `RWLock`: `try/finally` correcto, eliminado `bare except`.
- **B2** [`swarm.py`] `SwarmScheduler.tick()`: TOCTOU corregido extendiendo el lock.
- **B3** [`nectar.py`] Validación de `decay_rate` y `diffusion_rate` en `__init__`.
- **B4** [`resilience.py`] `_conduct_election` ahora exige quórum mayoritario y retorna `None` si no se alcanza (antes podía elegir reina con minoría).
- **B5** [`memory.py`] `PollenCache.put()` resta los bytes del valor antiguo **antes** del bucle de evicción (antes provocaba evicciones espurias al reemplazar una clave existente).
- **B7** [`metrics.py`] Buckets de `Histogram` ahora respetan la convención cumulativa de Prometheus (verificado por test).
- **B8** [`resilience.py`] `_repair_neighbor_link`: `try/except` alrededor de la búsqueda en `HexDirection` para tolerar nombres de dirección inválidos.

#### Bugs latentes descubiertos durante el testing (no estaban en el roadmap)
- **B2.5** [`swarm.py`] `SwarmScheduler.tick()` no limpiaba `_task_index` junto con `_task_queue`, provocando una fuga de memoria en runs largos.
- **B9** [`metrics.py`] 10 call sites accedían a `cell._pheromone_level` (atributo privado inexistente) en vez de la property pública `cell.pheromone_level`. Cualquier llamada a métricas habría lanzado `AttributeError` en runtime.
- **B10** [`core.py`] `HexCoord` usaba `@cached_property` con `@dataclass(frozen=True, slots=True)`, combinación que prohíbe `__dict__`. `HexCoord.cube`, `.array` y `.magnitude` lanzaban `TypeError` garantizado en cada acceso. Reemplazado por `@property` (la pérdida de cache es despreciable: cómputos O(1) sobre dos enteros).

> B9 y B10 demuestran el valor del esfuerzo de testing: ambos eran fallos de
> runtime garantizados que ningún test previo cubría.

### Added

- **`tests/test_memory.py`** — 71 tests, cobertura `memory.py` 94%. Incluye verificación de B5 (`test_replace_key_does_not_trigger_spurious_eviction`).
- **`tests/test_metrics.py`** — 76 tests, cobertura `metrics.py` 95%. Verifica convención cumulativa de Prometheus para `Histogram`.
- **`tests/test_resilience.py`** — 75 tests, cobertura `resilience.py` 83%. Incluye verificación de B4 y B8.
- **`tests/test_swarm.py`** — 65 tests, cobertura `swarm.py` 89%. Incluye verificación de B2.5 (`test_b2_5_no_leak_after_many_cycles`).
- **`tests/test_property.py`** — 53 tests Hypothesis cubriendo:
  - Álgebra de `HexCoord`: invariante cúbica `q+r+s=0`, simetría/triángulo de distancias, conmutatividad/asociatividad de suma, identidad/inverso, rotación 6×60° = identidad, anillos de tamaño `6r`, hexágono lleno de tamaño `1+3r(r+1)`, `lerp`, etc.
  - Semántica de `PheromoneField`/`PheromoneDeposit`: clamp `[0, 1]`, monotonía bajo cap, decay nunca incrementa intensidad, `total_intensity == sum`, `dominant_type` retorna ptype con máxima intensidad.
- **`snapshot/PHASE_01_CLOSURE.md`** — reporte completo de cierre de Fase 1.
- **`snapshot/bandit_phase01.json`** — escaneo de seguridad: 0 HIGH, 3 MEDIUM (todos `pickle`, planificados para Fase 2 con `mscs`), 4 LOW.
- **`snapshot/pip_audit_phase01.txt`** — auditoría de dependencias: 0 vulnerabilidades.
- **`ROADMAP.md`** — marcado FASE 1 como cerrada con resumen de resultados.
- **`.gitignore`** — añadidos `.hypothesis/` y `.claude/`.

### Deferred

- **B6** (TOCTOU en `swarm.py`/`resilience.py` cargando `load`) — clasificado como no bloqueante; se aborda en una fase posterior.
- Cobertura global 71% (objetivo era 75%). Los 4 módulos críticos superan el 80%; las brechas remanentes están en `core.py`, `bridge.py` y `nectar.py`, fuera del scope de Fase 1.

---

## [1.0.0] — 2026-03-XX (baseline)

**Snapshot inicial preservado** previo al inicio del roadmap de 10 fases.

- Tag preservado: `v1.0.0-baseline`
- Branch preservada: `baseline/v1.0.0`
- Documentación del estado: [snapshot/SNAPSHOT.md](snapshot/SNAPSHOT.md)

### Estado

- 8 módulos Python, ~10.557 LOC.
- Tests existentes: `test_core.py`, `test_nectar.py`, `test_bridge.py`, `test_heavy.py`.
- Sin tests directos: `memory.py`, `resilience.py`, `metrics.py`, `swarm.py`.
- Cobertura estimada: 30–40%.
- Auditoría inicial: 3 bugs críticos, 5 altos, 4 medios, 3 bajos (15 totales).

[1.2.0-phase02]: https://github.com/ElEscribanoSilente/Honeycomb-Optimized-Computing/releases/tag/v1.2.0-phase02
[1.3.0-phase03]: https://github.com/esraderey/Honeycomb-Optimized-Computing/releases/tag/v1.3.0-phase03
[1.2.0-phase02]: https://github.com/esraderey/Honeycomb-Optimized-Computing/releases/tag/v1.2.0-phase02
[1.1.0-phase01]: https://github.com/ElEscribanoSilente/Honeycomb-Optimized-Computing/releases/tag/v1.1.0-phase01
[1.0.0]: https://github.com/ElEscribanoSilente/Honeycomb-Optimized-Computing/releases/tag/v1.0.0-baseline
