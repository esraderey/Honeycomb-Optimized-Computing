# 🐝⚔️ Enjambre de Guerra

**Stress test suite for HOC.** Lo que el panal aguanta cuando el
ambiente se pone hostil. Pruebas que **no** corren en CI por defecto
porque son lentas, agresivas con memoria/CPU, o tienen flakiness
inherente al ser stress-driven.

## Cuándo correrlo

- Antes de un release mayor (Phase closure que va a producción).
- Después de tocar paths críticos: scheduler tick, async event loop,
  checkpoint format, sandbox.
- Cuando se sospecha de un leak / regresión de perf que la suite
  default no revela.
- Para validar que los SLAs implícitos se mantienen
  (latencia tick, throughput sostenido, tasks_dropped bounded).

## Cómo correrlo

```bash
# Suite completa (lento, ~5-10 min en hardware decente)
pytest enjambre_de_guerra/ -v

# Solo una categoría
pytest enjambre_de_guerra/test_throughput_burst.py -v

# Excluir los más lentos
pytest enjambre_de_guerra/ -v -m "not slow"

# Sólo los marcados slow (corre antes de release)
pytest enjambre_de_guerra/ -v -m slow

# Con timeout estricto
pytest enjambre_de_guerra/ -v --timeout=120
```

La carpeta está excluida del descubrimiento automático
(`norecursedirs` en `pyproject.toml`), así que `pytest` sin args
**no** la ejecuta. Hay que invocarla explícitamente.

## Categorías

| Archivo | Qué estresa |
|---------|-------------|
| `test_throughput_burst.py` | Submit 50K tareas, drain, validate counters |
| `test_concurrent_grids.py` | N=50 grids gathered simultáneamente |
| `test_concurrent_mutations.py` | submit/tick/cancel en threads concurrentes |
| `test_persistence_endurance.py` | 1000 checkpoints back-to-back, large grid roundtrip |
| `test_resilience_chaos.py` | Random cell kills durante tick, queen succession bajo carga |
| `test_sandbox_burst.py` | 100 sandboxed tasks consecutivos (POSIX) |
| `test_backpressure_extreme.py` | 100K submissions con drop_oldest |
| `test_pheromone_dos.py` | 50K deposits en coords distintas (verifica LRU bound) |
| `test_state_machine_fuzz.py` | Hypothesis fuzz de FSM transitions |
| `test_long_running.py` | 5000 ticks endurance + memory tracking |
| `test_invariants.py` | Cross-system property tests (Hypothesis) |

## Markers

- `@pytest.mark.stress` — todos los tests aquí lo llevan.
- `@pytest.mark.slow` — los que tardan > 30s individuales.
- `@pytest.mark.posix_only` — sandbox tests (skip en Windows).

## Filosofía del enjambre de guerra

Los stress tests no garantizan que un sistema funcione bien — solo
demuestran que **falla de la forma esperada bajo carga conocida**.
Cada test aquí codifica una hipótesis explícita ("la queue dropea
correctamente bajo X carga", "el sandbox aísla SIGSEGV en N
intentos consecutivos") y la pone a prueba con el peor caso
razonable.

Si un test aquí empieza a fallar **intermitentemente** después de
haber pasado consistentemente, eso es señal de regresión real, no
de flakiness — investigar antes de hacer skip.

## Salida esperada

```
======================== Enjambre de Guerra ========================
test_throughput_burst.py::test_50k_burst_completes ............ PASS (8.2s)
test_throughput_burst.py::test_50k_burst_with_drop_oldest ..... PASS (4.1s)
test_concurrent_grids.py::test_50_grids_no_cross_contam ....... PASS (12.0s)
test_persistence_endurance.py::test_1000_checkpoints_back_to_back PASS (15.4s)
test_resilience_chaos.py::test_random_cell_kills_during_tick .. PASS (6.8s)
test_sandbox_burst.py::test_100_sandboxed_tasks ........... SKIP (Windows)
test_backpressure_extreme.py::test_100k_drops_99900 ........... PASS (3.2s)
test_pheromone_dos.py::test_50k_deposits_lru_bounded .......... PASS (5.5s)
test_state_machine_fuzz.py::test_random_transitions_no_corruption PASS (10.0s)
test_long_running.py::test_5000_ticks_no_perf_drift ........... PASS (45.2s)
test_invariants.py::test_<various> ............................ PASS
====================================================================
```

Si un test pasa de SLA o el counter `tasks_dropped` se desincroniza,
el panal está roto. Investigar antes de tocar otra cosa.
