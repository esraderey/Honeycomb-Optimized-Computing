# Phase 8 multi-nodo — Jepsen-style prep notes

**Status**: pre-Phase-8 planning notes (2026-04-29).
Esto NO es un ADR — es scratch para informar el design de Phase 8.
Cuando Phase 8 arranque, este documento se reemplazará por
PHASE_08_BRIEF.md + ADRs específicos (linearizability model,
partition policy, Jepsen integration).

## Por qué esto importa

El enjambre_de_guerra/ stress suite valida que HOC v2.0.0 aguanta
lo que aguanta **single-node**. Phase 8 introduce gRPC + mTLS entre
instancias, federación de subpanales, queen global vs queens
regionales. Las clases de bugs cambian:

| Single-node bugs | Distributed bugs |
|------------------|------------------|
| Race en RWLock | Linearizability violation bajo partition |
| Heap heapify corrupto | Stale-leader split-brain |
| _task_index leak | Phantom reads después de re-elección |
| BehaviorIndex tombstone overflow | Lost writes durante quorum reformation |
| FSM transition corrupta | Clock skew rompe TTL en pheromone deposits |

Stress tests escalados (más threads, más grids, más ticks) **no**
encuentran los del lado derecho. Hace falta otro tipo de testing:
**property testing distribuido** estilo [Jepsen](https://jepsen.io/).

## Lo mínimo a leer antes de Phase 8

Esraderey, antes de escribir una línea de gRPC para HOC, leé:

- **[Aphyr's Jepsen analyses](https://aphyr.com/tags/jepsen)**.
  Empezar por: etcd / Cassandra / MongoDB / Redis. Cada uno es un
  caso de estudio sobre cómo un sistema "production-ready" puede
  perder writes bajo conditions específicas. La estructura recurrente
  del análisis es la plantilla mental para Phase 8.
- **["Jepsen 5 — How to test a distributed system"](https://www.youtube.com/watch?v=5_a9C-ZNhNI)**
  (charla). Resume el método: model the system → spec the
  invariants → inject faults → verify history. ~40 min, vale más
  que cualquier libro de "best practices distributed".
- **["Linearizability vs Serializability"](http://www.bailis.org/blog/linearizability-versus-serializability/)**
  (Peter Bailis). Phase 8 va a forzarte a elegir un consistency
  model — leer esto antes que tomar la decisión.
- **CAP teorema** y, más útil que CAP, **PACELC** (PA / EL trade-off
  además de CAP). El roadmap menciona "AP — favoreceríamos
  disponibilidad", pero PACELC obliga a también especificar el
  trade-off latencia-vs-consistencia bajo operación normal.

## Lo que el enjambre_de_guerra actual NO puede testear

1. **Network partitions arbitrarias.** En single-process, los grids
   se hablan via memoria compartida. Un partition asimétrico (A→B
   pasa pero B→A no) requiere control sobre los sockets/firewalls.
2. **Clock skew.** Local-only, todos los grids leen `time.time()`
   del mismo wall clock. En distributed, los nodos discrepan en
   ms-segundos.
3. **Half-open TCP connections.** Un connection que el OS cree
   abierto pero que el peer ya cerró. Pheromone deposits enviados
   ahí se pierden silenciosamente.
4. **Replay attacks across nodes.** El HMAC de Phase 2 es por
   instance; un atacante con acceso a la wire podría re-enviar
   un PheromoneDeposit firmado.
5. **Queen succession race entre regiones.** Si dos regiones
   declaran queen succession simultánea por una partition, el
   protocolo Raft-like de Phase 2 no garantiza que ambas elijan
   al mismo nuevo líder global.

## El stack mínimo para Jepsen-en-HOC

Phase 8 va a necesitar (en orden de incremental cost):

### Nivel 0 — model spec
- Definir formalmente los invariants distributed: "task X submitted
  a un grid eventualmente termina en COMPLETED en exactly-one grid",
  "queen state es eventually consistent across nodes", etc.
- Property: "history of operations es linearizable contra el
  spec".

### Nivel 1 — chaos injection sin Jepsen
- Reusar el harness multiprocessing del sandbox; correr 3 grids en
  procesos separados, comunicándose via `multiprocessing.Pipe` o
  `socketpair`.
- Inyectar faults: kill -9 en uno de ellos, corrupt bytes en el
  pipe, delay injection (sleep antes de send).
- Verificar invariants post-fault.
- Costo: ~2 semanas de scaffolding.

### Nivel 2 — Jepsen real
- [maelstrom](https://github.com/jepsen-io/maelstrom) (Jepsen's
  workbench) es el on-ramp. Soporta Python clients via stdin/stdout
  IPC, sin la barra de entrada de Clojure-puro.
- HOC implementa el protocolo maelstrom (init → echo → broadcast →
  ...) y maelstrom inyecta partitions / latency / drop.
- Linearizability checker de Jepsen (Knossos) verifica histories.
- Costo: ~4-6 semanas, más Phase 8 mismo.

### Nivel 3 — Jepsen Clojure
- Para certificación seria (papers, customer claims), traducir el
  test harness a Clojure + Jepsen library.
- Costo: ~3 meses adicionales. Probablemente innecesario para
  HOC v3.0.

## El "no" más importante de Phase 8

**No empezar con gRPC + service discovery + chaos engineering al
mismo tiempo.** El roadmap de Phase 8 lista todos juntos. La
realidad operativa: una de esas tres cosas tiene que estar 100%
estable antes de stackear las otras.

Recomendación:

1. **Phase 8.1**: solo el RPC layer. mscs schemas para wire format,
   gRPC bindings, mTLS. UN solo invariant: round-trip serialise →
   send → deserialise == identity. Sin federation, sin succession
   distribuida.
2. **Phase 8.2**: federation entre 2 nodos. Pheromone exchange
   en bordes. UN solo invariant: pheromone intensity es eventually
   consistent (con clock-skew tolerance documentada).
3. **Phase 8.3**: queen succession multi-nodo. Aquí es donde
   Jepsen-style testing se vuelve no-opcional.
4. **Phase 8.4**: chaos engineering / failure modes documentados.

Cada sub-phase debe tener su propio enjambre_de_guerra/ folder
(nombre TBD — `crisis_distribuida/`?) con tests específicos al
modelo de fault de ese sub-phase.

## El reality check

HOC v2.0.0 single-node es probablemente el último punto donde el
proyecto puede declararse "complete" sin ser distribuido. Phase 8
es scope-creep si no hay un user real esperando multi-nodo. Los
sistemas distribuidos consumen tiempo en proporción a su footprint
de error: 10× más LOC, 100× más bugs sutiles, 1000× más debugging
horror stories.

Si Vent / CAMV no demanda multi-nodo en los próximos 3-6 meses,
considerar **detener HOC en v2.0.0** y migrar tu energía al
framework consumidor. v2.0.0 ya tiene async + persistencia +
sandbox + FSMs formales — es un módulo serio. Phase 8-10 es
otra cosa.

Esta nota existe para que cuando Phase 8 efectivamente arranque,
no sea por momentum del roadmap sino por necesidad real, con
los ojos abiertos al costo.

## Referencias

- [Jepsen project](https://jepsen.io/)
- [Aphyr's blog](https://aphyr.com/)
- [Maelstrom](https://github.com/jepsen-io/maelstrom)
- [Knossos linearizability checker](https://github.com/jepsen-io/knossos)
- [PACELC paper (Abadi 2012)](http://www.cs.umd.edu/~abadi/papers/abadi-pacelc.pdf)
- [Marc Brooker — "Tools and tactics for distributed systems"](https://brooker.co.za/blog/)
- [Kingsbury & Bailis — "The network is reliable" (anti-fallacy talk)](https://queue.acm.org/detail.cfm?id=2655736)
