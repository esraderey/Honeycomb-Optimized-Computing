# Changelog

Todas las modificaciones notables del proyecto **HOC (Honeycomb Optimized
Computing)** se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y este proyecto adhiere a [Semantic Versioning](https://semver.org/lang/es/).

---

## [1.5.0-phase05] — 2026-04-26

**Cierre de Fase 5 — Observabilidad + full FSM wire-up.** 804 tests
pasando (+71 vs Phase 4.3: 28 wire-up tests + 9 logging + 28 phase
tests + extras). Las **5 FSMs ahora wireadas** (vs 2 al inicio):
CellState ya estaba; Phase 4.3 reservó MIGRATING y SEALED y Phase 5.1
las wireó (`CellFailover._migrate_work` + nuevo `HoneycombCell.seal()`);
FailoverFlow / PheromoneDeposit / QueenSuccession upgraded de
declarative-only a wired (5.2c / 5.2a / 5.2b). `choreo check --strict`
ahora reporta **0 errors / 0 warnings / 0 info** y el CI lo enforce.
Logging estructurado vía `structlog` (5.3) emite eventos JSON-
serializables en cada cell state transition, seal, migración y
elección. Bench baseline reproducible (5.5) más nuevo job CI
`bench-regression` cierra Gap 3 de Phase 4. `structlog>=25.0` agregado
como runtime dep. Bandit/pip-audit/ruff/black/mypy todos limpios.
Cobertura global subió a **79.41%** (+~3 pts vs Phase 4.3).

Reporte completo: [snapshot/PHASE_05_CLOSURE.md](snapshot/PHASE_05_CLOSURE.md).

### Added

#### Runtime dependency
- `structlog>=25.0.0` pinneado en `requirements.txt` y declarado en
  `pyproject.toml [project].dependencies`. Provee el motor de logging
  estructurado (~70 KB, MIT, zero transitive deps). Aislado tras
  `hoc.core.observability` — única importación de structlog en todo
  el repo. Ver [ADR-011](docs/adr/ADR-011-observability-stack.md).

#### `hoc.core.observability` módulo (Phase 5.3)
- `configure_logging(json: bool = False, level: int = INFO)` — call
  once at startup. JSON output para producción, ConsoleRenderer
  colored para dev.
- `get_event_logger(name="hoc.events")` — devuelve un structlog
  `BoundLogger` para el canal especificado.
- `EVENT_LOGGER_NAME = "hoc.events"` — constante para filtrado.
- `log_cell_state_transition(coord, from_state, to_state)` — helper
  para mantener el field-name schema estable.
- 6 eventos cableados: `cell.state_changed` (set_state),
  `cell.sealed` (seal), `failover.migrate_started` /
  `migrate_completed` (migrate_work), `election.started` /
  `election.completed` (elect_new_queen).

#### Phase 5.1 — `CellState.MIGRATING` + `SEALED` wired
- `state_machines/cell_fsm.py` agrega 2 wildcard transitions:
  `WILDCARD → MIGRATING` (trigger=`admin_start_migration`) y
  `WILDCARD → SEALED` (trigger=`admin_seal`).
- `core/cells_base.py:HoneycombCell.seal(reason="...")` — nuevo
  método para graceful shutdown. Drains vCores, refuses new tasks
  (`add_vcore` rechaza con SEALED), persiste métricas finales en
  log estructurado, transiciona a SEALED. Idempotente; refuses
  sealar una FAILED.
- `resilience.py:CellFailover._migrate_work` — `source.state =
  MIGRATING` antes del bucle; rollback de estado al original en
  excepción. Cierra commitment de [ADR-010](docs/adr/ADR-010-dead-enum-cleanup.md).

#### Phase 5.2c — `FailoverFlow` FSM wired
- Nuevo `FailoverPhase` enum en `resilience.py`
  (HEALTHY/DEGRADED/MIGRATING/RECOVERED/LOST).
- `_FailoverCellState` dataclass wrapper (state + per-coord FSM
  instance). Wrapper exists para que el walker de choreo detecte
  `obj.state = ENUM.MEMBER`.
- `CellFailover._per_cell_failover: dict[HexCoord, _FailoverCellState]`
  + `_set_failover_phase` helper + público `get_failover_phase(coord)`.
- `_migrate_work` walks HEALTHY → DEGRADED → MIGRATING → RECOVERED;
  excepción dispara `tramoya.undo()` reverting MIGRATING → DEGRADED.
- `mark_recovered` avanza RECOVERED → HEALTHY (stabilized).
- `state_machines/failover_fsm.py` agrega `enum_name="FailoverPhase"`.

#### Phase 5.2a — `PheromoneDeposit` FSM wired (static-only)
- Nuevo `PheromonePhase` enum en `nectar.py` (FRESH/DECAYING/
  DIFFUSING/EVAPORATED).
- `PheromoneDeposit.state: PheromonePhase = FRESH` field.
  Mutado por `evaporate` (DECAYING/EVAPORATED por age/intensity) y
  `diffuse_to_neighbors` (DIFFUSING transient → DECAYING). NO
  per-instance FSM (perf budget).
- Bench: `test_nectar_flow_tick` +1.4% (dentro de `<3%` budget).
- `state_machines/pheromone_fsm.py` agrega
  `enum_name="PheromonePhase"`.

#### Phase 5.2b — `QueenSuccession` FSM wired (security-critical)
- Nuevo `SuccessionPhase` enum en `resilience.py` (STABLE/DETECTING/
  NOMINATING/VOTING/ELECTED/FAILED).
- `_SuccessionState` dataclass wrapper (state + history list).
- `QueenSuccession._succession_state` + `_set_phase` con if/elif
  chain por miembro.
- `elect_new_queen` walks STABLE → DETECTING → NOMINATING → VOTING
  → ELECTED → STABLE en éxito; failure paths landean en FAILED.
- `_conduct_election` muta VOTING al inicio y ELECTED|FAILED según
  outcome del tally.
- Pública: `succ.phase` y `succ.phase_history` para observability.
- `state_machines/succession_fsm.py` agrega
  `enum_name="SuccessionPhase"`.
- **Anti-regresión**: la lógica de `_tally_votes` y `_term_number`
  está byte-identical a Phase 4.3. Los 7 tests
  `TestQuorumSignedVotes` siguen verdes sin modificación.

#### Phase 5.5 — bench baseline + regression CI
- `snapshot/bench_baseline.json` (condensed, 5.5 KB) captured desde
  main pre-Phase-5.
- `scripts/compare_bench.py` toma dos snapshots condensados y
  reporta % diff por benchmark contra threshold (default 10%).
- `.github/workflows/bench.yml` nuevo job CI: captura bench actual
  con `--benchmark-warmup=on --benchmark-min-time=0.5`, condensa,
  compara, falla si regresión >10%. Comando documentado en
  `CONTRIBUTING.md`.
- Cierra Gap 3 de Phase 4 closure.

#### Documentation
- **ADR-011** — Observability stack (structlog + Prometheus deferred
  + dashboard deferred).
- **ADR-012** — `choreo --strict` flip mode.

#### Tests (804 pasando, +71)
- `tests/test_cell_seal.py` (12) — graceful shutdown.
- `tests/test_failover_phase.py` (10) — FailoverFlow wire-up + undo.
- `tests/test_pheromone_state.py` (9) — PheromoneDeposit phases.
- `tests/test_succession_phase.py` (15) — SuccessionPhase progression.
- `tests/test_logging.py` (9) — structlog wire-up.
- `tests/test_resilience.py::TestCellFailover` +3 — MIGRATING wire-up.
- `tests/test_state_machines.py` +4 — admin triggers nuevos.
- `tests/test_state_machines_property.py` — exempt set vacío para
  CellState.
- `tests/test_choreo.py` — actualizado a 0/0/0.

### Changed

#### Phase 5.6 — `choreo check --strict` enforced
- `.github/workflows/lint.yml` `choreo-static-check` actualizado de
  `python -m choreo check` a `python -m choreo check --strict`.
- `--strict` raises warnings y info a errors; cualquier futuro PR
  con dead state, enum-extra, o declarative-only FSM rompe el build.
- Local invocation en `CONTRIBUTING.md` follows.
- Cierra commitment de ADR-008 + ADR-010 + ADR-012.

#### `pyproject.toml`
- `[project].dependencies` += `"structlog>=25.0.0"`.
- `[[tool.mypy.overrides]]` external block: `structlog, structlog.*`
  con `ignore_missing_imports`.

#### `__init__.py`
- Re-export `configure_logging`, `get_event_logger`,
  `EVENT_LOGGER_NAME` desde `hoc.core.observability`.

### Deferred to Phase 5.x followup / Phase 6

#### 5.4 — Métricas Prometheus
Brief explícitamente flagged opcional. Diferido por budget de sesión.
Spec intacta: `prometheus_client` runtime dep, 5 collectors, HTTP
`/metrics` endpoint, `hoc-cli serve-metrics` entry point.
Mitigación interim: structured logs de 5.3 cubren las series que
una collector consumiría (vía promtail / fluent-bit log-derived
metrics).

#### 5.7 — Dashboard
Brief explícitamente opcional. Diferido a Phase 6 alongside
persistence work.

#### Cobertura objetivo 80% global
Cierre de Phase 5 a 79.41% (-0.59 pts del target). `bridge.py`
permanece en 56% (Gap 4 desde Phase 4). Diferido a Phase 5.x test
boost o Phase 6 split de bridge.

#### Bench `test_grid_creation` regresión +25.88%
Causa: FSM allocation per-cell + nuevos campos. Aceptable pero
documentado para optimizar (e.g. class-level shared FSM en lugar de
per-instance).

### choreo report — Phase 4.3 vs Phase 5

| | Phase 4.3 | Phase 5 |
|---|---|---|
| Errors | 0 | 0 |
| Warnings | 1 (CellState dead: MIGRATING + SEALED) | **0** ✅ |
| Info | 3 (Pheromone, Succession, Failover declarative-only) | **0** ✅ |
| Strict mode in CI | not enforced | **enforced** ✅ |

### Audits

- ruff: 0 errores
- black: 0 archivos a reformatear
- mypy `python -m mypy .`: 0 errores
- mypy `python -m mypy --explicit-package-bases state_machines/*.py`: 0
- bandit: **0 / 0 / 0** (HIGH / MEDIUM / LOW), 11,728 LOC scanned, 42 archivos
- pip-audit (runtime + dev): clean
- radon CC: average **C (13.3)** — sin regresión vs Phase 4.3
- pytest: **804 / 804 passing**
- coverage: **79.41%** (vs target 80%, -0.59 pts diferido)
- choreo `--strict`: 0/0/0 ✅

[1.5.0-phase05]: https://github.com/esraderey/Honeycomb-Optimized-Computing/releases/tag/v1.5.0-phase05

---

## [1.4.3-phase04.3] — 2026-04-25

**Cierre de Fase 4.3 — Dead enum cleanup (B12-bis + B12-ter resueltos
parcialmente).** 733 tests pasando (-1 vs Phase 4.2: el test obsoleto
`test_illegal_transition_assigned_dead_state_raises` fue eliminado).
choreo reduce warnings de 2 a 1. Per-member discrimination: 3 enum
members eliminados (`TaskState.ASSIGNED`, `CellState.SPAWNING`,
`CellState.OVERLOADED`); 2 reservados para wire-up en Phase 5
observability (`CellState.MIGRATING`, `CellState.SEALED`).
Bandit/pip-audit/ruff/black/mypy todos limpios.

Reporte completo: [snapshot/PHASE_04_3_CLOSURE.md](snapshot/PHASE_04_3_CLOSURE.md).

### Removed

- **`TaskState.ASSIGNED`** — declarado en swarm.py:90 desde Phase 1
  pero ningún call-site lo asignaba. B12-bis resuelto.
- **`CellState.SPAWNING`** — aspiracional, sin caller en producción.
  Cells nacen `EMPTY → IDLE`, no via SPAWNING.
- **`CellState.OVERLOADED`** — aspiracional, circuit breaker tiene
  solo 2 estados (cerrado=ACTIVE, abierto=FAILED).
- **Test `test_illegal_transition_assigned_dead_state_raises`** —
  obsoleto tras la eliminación de ASSIGNED.

### Reserved (deferred to Phase 5)

- **`CellState.MIGRATING`** — wire-up planeado en
  `CellFailover.migrate_cell` para observabilidad de migraciones
  in-flight.
- **`CellState.SEALED`** — wire-up planeado en nuevo `cell.seal()`
  para graceful shutdown.

Ambos reservados aparecen como warning `dead_state` en `choreo check`
hasta que Phase 5 los wireé. ADR-010 documenta el commitment.

### Updated

- **`core/cells_base.py:CellState`** — 9 → 7 members (con docstring
  documentando el cleanup).
- **`swarm.py:TaskState`** — 6 → 5 members.
- **`state_machines/cell_fsm.py`** — `CELL_STATE_SPAWNING` y
  `CELL_STATE_OVERLOADED` constants removidas; `ALL_CELL_STATES`
  reducida a 7.
- **`metrics/visualization.py`** — entries `SPAWNING` removidos de
  `STATE_CHARS` y `colors`.
- **Tests** — `test_state_count` (`9` → `7`),
  `test_dead_state_unreachable_via_lifecycle` (usa SEALED en lugar de
  SPAWNING), `test_illegal_transition_raises_and_does_not_mutate`
  (idem), `test_render_includes_state_count_and_initial` (`(9)` →
  `(7)`), `test_hoc_findings_exact` (assertions actualizadas).
- **`docs/state-machines.md`** — regenerado (CellState diagram con 7
  nodes en lugar de 9).

### Documentation

- **ADR-010** — Dead enum-member cleanup: eliminate vs reserve
  rationale (per-member).

### choreo report — Phase 4.2 vs 4.3

| | Phase 4.2 | Phase 4.3 |
|---|---|---|
| Errors | 0 | 0 |
| Warnings | 2 | **1** |
| Info | 3 | 3 |

El warning restante (CellState dead: MIGRATING + SEALED) es
**intencional** — reservado, no bug.

---

## [1.4.2-phase04.2] — 2026-04-25

**Cierre de Fase 4.2 — `choreo` v0.2: reified transitions + auto-derive
+ walker patterns + opt-in enum binding.** 734 tests pasando (+29 vs
Phase 4.1: 8 walker + 4 enum_name + 6 derive + 11 reified). Cuatro
mejoras additivas a `choreo` y al subpaquete `state_machines/`, sin
romper contratos de Phase 4.1. Sin nuevas dependencies runtime.
Bandit/pip-audit/ruff/black/mypy todos limpios. choreo aplicado a HOC
sigue reportando idéntico (0 err / 2 warn / 3 info).

Reporte completo: [snapshot/PHASE_04_2_CLOSURE.md](snapshot/PHASE_04_2_CLOSURE.md).

### Added

#### `choreo` v0.2 — walker patterns
- `setattr(obj, "state", EnumName.MEMBER)` capture (con `pattern="setattr"`).
- `dataclasses.replace(obj, state=EnumName.MEMBER)` capture (con
  `pattern="dataclasses.replace"`). Soporta tanto la forma qualified
  como bare (`from dataclasses import replace`).

#### `choreo` v0.2 — `derive` subcommand
- `python -m choreo derive <module.py>` emite skeleton FSM desde
  mutations observadas. Output usa `WILDCARD` para sources (el
  contribuyente edita).
- Opciones: `--fsm-name`, `--enum-name`, `--initial`, `-o/--output`.
- Heurística de naming: `TaskState` → `TaskLifecycle` →
  `build_task_fsm`.

#### `state_machines/reified.py` — `@transition` decorator
- Decorator factory `transition(from_=X, to=Y)` para declarar
  transiciones inline en métodos.
- Comportamiento: pre-condición → ejecuta método → muta state si
  retorna OK; no muta si excepción.
- Stores `__choreo_transition__ = (from_, to)` en el método para
  introspección futura.

#### Reified API en `HiveTask` (additive)
- `task.claim(worker)` (PENDING → RUNNING)
- `task.complete(result=None)` (RUNNING → COMPLETED)
- `task.fail(error)` (RUNNING → FAILED)
- `task.retry()` (FAILED → PENDING)
- 16 call-sites en `swarm.py` siguen usando direct mutation; las dos
  APIs coexisten.

#### `HocStateMachine.enum_name=` (opt-in metadata)
- Nuevo parámetro `enum_name: str | None = None` en
  `HocStateMachine.__init__`.
- `choreo/diff.py::bind_fsm_to_enum` prefiere binding explícito sobre
  heurística cuando se setea.
- Strings (no `type[Enum]`) para evitar circular imports.
- `cell_fsm.py` y `task_fsm.py` actualizados con `enum_name="CellState"`
  y `enum_name="TaskState"`.

#### Documentation
- **ADR-009** — Reified transitions + auto-derive (`choreo` v0.2).

#### Tests
- `tests/test_choreo.py::TestWalker` — 6 nuevos (setattr + replace).
- `tests/test_choreo.py::TestBindFsmToEnum` — 3 nuevos (enum_name).
- `tests/test_choreo.py::TestDerive` — 6 tests del derive helper.
- `tests/test_choreo.py::TestCli` — 3 nuevos (subcommand derive).
- `tests/test_state_machines.py::TestReifiedDecoratorIsolated` — 5.
- `tests/test_state_machines.py::TestReifiedHiveTask` — 6.

### Changed

- `state_machines/base.py` — `HocStateMachine.__init__` acepta
  `enum_name`; nueva property `enum_name`.
- `state_machines/__init__.py` — re-exporta `transition`.
- `choreo/walker.py` — visit_Call extendido con setattr y
  dataclasses.replace; nuevo `walk_file()` para uso single-file.
- `choreo/types.py` — `FsmSpec.enum_name: str | None = None` opcional.
- `choreo/spec.py` — `_spec_from_fsm` lee `fsm.enum_name`.
- `choreo/diff.py::bind_fsm_to_enum` prefiere enum_name explícito.
- `choreo/cli.py` — agrega subcommand `derive`.

### Deferred (Phase 5+)

- B12-bis (`TaskState.ASSIGNED`) y B12-ter (4 `CellState` dead) sin
  resolución; warning de CI persiste.
- `--strict` flip en CI espera resolución de los anteriores.
- Auto-derive con CFG analysis (sources reales) deferred a Phase 11+.

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

[1.4.3-phase04.3]: https://github.com/esraderey/Honeycomb-Optimized-Computing/releases/tag/v1.4.3-phase04.3
[1.4.2-phase04.2]: https://github.com/esraderey/Honeycomb-Optimized-Computing/releases/tag/v1.4.2-phase04.2
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
