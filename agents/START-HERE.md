# START HERE — De la plantilla al equipo operando

Manual para personas. Los agentes no leen este documento: reciben su card y su packet.

## 0. Qué significa instanciar

Este repositorio tiene capacidades, no producto. Instanciar es fijar el **qué** sin tocar
los roles:

| Documento | Plantilla | Quién lo escribe |
|---|---|---|
| Brief del producto | `templates/product-brief.md` | `discovery-lead` |
| Equipo propuesto | `templates/roster.yaml` | `discovery-lead` |
| Mapa de dominios | `templates/domain.yaml` | `discovery-lead` + `software-architect` |
| Especificación por funcionalidad | `templates/spec.yaml` | `spec-owner` |
| Tareas | `templates/feature.yaml` | tech leads |
| Tablero | `templates/backlog.json` | `orchestrator` |
| Packets | `templates/packet.json` | `orchestrator` |

Si para arrancar has tenido que editar un rol, lo que faltaba era un documento de
instanciación.

## 0b. Instalarlo dentro de un producto

El sistema vive en `agents/`, en la raíz del producto. Ese directorio es el ancla: los
documentos internos y el estado de sesiones se resuelven contra él, no contra el directorio
desde el que se ejecute una orden.

- El estado (`sessions.json`, `sessions.jsonl` y `sessions.json.lock`) se crea **junto a
  `agents/`**. No se versiona: añade esas tres rutas al `.gitignore` del producto.
- Puedes lanzar el CLI desde la raíz o desde `specs/`: la sesión es la misma. Para otro
  emplazamiento, pasa `--registry <ruta>`.
- Si el sistema se monta como copia o submódulo en un repositorio mayor, `agents/` sigue siendo
  la raíz del sistema y el producto aporta brief, roster, dominios, especificaciones y tablero.

## 1. Preflight

- [ ] Repositorio con rama principal protegida: PR obligatorio, sin push directo.
- [ ] Entorno de desarrollo reproducible (lo resuelve `devops-engineer` en la ola 0).
- [ ] Gestor de secretos decidido; nada de secretos en el repositorio.
- [ ] Tablero decidido (un JSON versionado basta al principio).
- [ ] `agents/` en la raíz del producto y el estado de sesiones fuera de git.
- [ ] Límite de workers simultáneos acordado, **uno por rol**. Empieza por dos roles distintos.
- [ ] Una persona que firma la aceptación final del producto.

## 2. Entrevista de descubrimiento

Lanza `discovery-lead` con la batería de preguntas de `templates/interview.yaml`. No es una
charla: es un interrogatorio amable para no construir lo que nadie pidió.

Lo que debe salir de ahí:

1. **Necesidad** en una frase y en palabras del usuario.
2. **Usuarios** concretos, con contexto y alternativas actuales.
3. **Alcance y no-alcance** de esta etapa.
4. **Convenciones**: dinero, fechas, zona horaria, idiomas, datos personales, accesibilidad.
5. **Métricas** de éxito con definición verificable.
6. **Restricciones** técnicas, legales y de equipo, citadas literalmente cuando no admiten
   interpretación.
7. **Equipo propuesto**: qué roles del catálogo se instancian, cuáles no hacen falta y
   cuáles habría que crear para este producto.

`discovery-lead` no decide: propone. La persona confirma. El resultado es el brief y el
roster.

## 3. Equipo y dominios

Del roster salen dos decisiones:

- **Qué roles existen.** Todos los del catálogo son opcionales salvo `orchestrator`,
  `product-owner`, `spec-owner` y los tech leads. Si falta una especialidad, `discovery-lead`
  la detecta y **`org-architect` la diseña**: decide si basta con dividir un rol existente,
  escribe la ficha y la card, y actualiza `registry.json` y `permissions.json` en el mismo
  cambio. Los roles condicionales habituales están listados en `agents/README.md`.
- **Qué dominios hay.** Uno por capacidad funcional acotada, cada uno con su
  `domain-owner`. Si un dominio no se explica sin nombrar tres módulos, no es un dominio.

## 4. Especificación por funcionalidad

Cada funcionalidad tiene **un** documento en `specs/`. Lo escribe `spec-owner` con
`templates/spec.yaml`, entrevistando a quien pide la funcionalidad sobre:

- qué debe poder hacer la persona, paso a paso;
- qué pasa en los casos raros: vacío, error, permiso, concurrencia, reintento;
- cómo interactúa con lo que ya existe;
- qué datos lee y escribe, y cuáles son personales;
- qué textos y qué idiomas hay;
- qué se prueba y cómo se sabrá que está bien.

La especificación la lee todo el equipo. Sin ella no hay tareas.

## 5. Del documento al trabajo

- Los tech leads parten la especificación en tareas (`templates/feature.yaml`).
- Cada tarea cita el criterio de aceptación que satisface y declara sus categorías y firmas.
- `orchestrator` agrupa en olas: sin dependencias abiertas y sin solapar superficies.
- Por cada tarea se emite un packet: qué lee, qué escribe, qué no toca. Se valida con
  `python3 agents/tools/session.py check --packet <packet.json>`: si pide una categoría que la
  matriz no concede al rol, no entra.
- El tablero se publica después de `python3 agents/tools/board.py lint --board <ruta>`.

## 6. Olas de arranque

| Ola | Roles | Resultado |
|---|---|---|
| 0 | `devops-engineer`, `qa-lead`, `security-engineer` | entorno, integración, criterio de listo y modelo de amenazas |
| 1 | `software-architect`, `database-engineer`, `structure-steward` | decisiones estructurales, esquema base, contratos y mapa de superficies con dueño |
| 2 | `ux-designer`, `ui-designer`, `content-writer` | flujos, sistema de diseño y textos base |
| 3 | tech leads + ingenieros + `domain-owner` | primera funcionalidad de punta a punta |
| 4 | `qa-automation`, `technical-writer`, `communications-lead`, `growth-analyst` | regresión, documentación, nota de entrega y medición |

No abras la ola 3 sin la 0 y la 1: sin entorno ni contratos, el trabajo se rehace.

## 7. Ciclo diario

- **Apertura**: `orchestrator` marca en curso, pregunta a `agents/tools/session.py decide` si
  reutiliza o rota la sesión del rol, asigna rama y packet, y publica el tablero.
- **Durante**: cada worker trabaja en su tarea y su superficie, ejecuta gates y abre PR. Si
  no sabe algo, **para y pregunta** (`policy/delivery.yaml#when_unsure`): no rellena el
  hueco con una suposición. `structure-steward` vigila que la estructura nueva quede
  declarada y con dueño.
- **Cierre**: tech lead revisa, `qa-lead` verifica, `product-owner` acepta,
  `technical-writer` documenta lo que alguien tendrá que usar, `communications-lead` publica
  y `orchestrator` cierra y registra la sesión con `session.py close --memory … --board …`:
  la sesión no cierra una tarea que el tablero no reconoce.
- **Relevo**: al cerrar cada ola, `orchestrator` deja `.orchestrator/handoff.md` con diez líneas.
  Si el trabajo ya no cabe sin releer medio repositorio, o la persona cambia de chat, se para
  ahí: handoff, tablero al día y sesión nueva. La nueva lee handoff, tablero, brief y roster;
  nunca el historial anterior.

## 7b. Sesiones: la decisión es una función

No se decide «a ojo» si se reutiliza un chat. Se ejecuta:

```bash
python3 agents/tools/session.py decide --role <rol> --spec specs/<feature>.spec.yaml \
  --model <modelo> --domain <dominio> --packet <packet.json> [--window N]
```

- **Se reutiliza mientras sea legal y económico.** No hay que justificar el REUSE: el historial
  ya está pagado y rotar exige escribir un handoff. Lo que impide la deriva son las
  comprobaciones, no rotar por costumbre.
- **Modelo y dominio son obligatorios** en la línea de órdenes: son las dos comprobaciones de
  legalidad que antes no existían si se omitía el argumento. Sin sesión abierta tampoco hay
  ancla, así que el ciclo empieza siempre por `session.py open`.
- `--continuation <tarea>` y `--follow-up` no autorizan nada: dejan escrito a qué hilo pertenece
  la tarea (retoque de algo ya cerrado, o tarea siguiente del mismo PR).
- Y además tiene que **compensar**: la herramienta compara bytes facturados. Reutilizar
  arrastra el historial en cada turno; rotar paga una lectura extra y empieza limpio.
- El presupuesto **no se escribe a mano**: `open --window N` declara la ventana del modelo y de
  ella salen los bytes cargables, los turnos y las tareas que caben. Los techos del perfil
  (`max_tasks_ceiling`, `max_turns_ceiling`) acotan la coherencia, no la capacidad: con 1 M de
  contexto el límite es el coste, no el espacio.
- **Los turnos se acumulan** de una tarea a la siguiente y el journal manda sobre `--turns`. Si
  el contador se reiniciara en cada cierre, el tope de turnos no se alcanzaría nunca.
- La calibración viene de un **perfil de entorno** (`agents/policy/providers.yaml`):
  `conservative` (por defecto, no asume caché), `prompt-cache` o lo que declare el producto.
  Si el perfil no existe, se aplica el conservador y la decisión sigue siendo válida. Cambiar
  de proveedor es cambiar de perfil, nunca de política.
- Los datos no se piden al editor ni al modelo: el worker registra hitos con
  `session.py record --event turn|read|write`, `stats` calibra la media por rol y
  `verify --packet --branch <rama>` compara el diff de la rama con el write set. Ver
  `policy/measurement.yaml`.
- Cada decisión deja un evento `decision` en `sessions.jsonl`. Si no hay evento, la decisión no
  existió: nadie tiene que creerse de memoria que se ejecutó.
- Código **0 = REUSE**: imprime el ancla de la huella y exactamente qué enviar (tarea, packet,
  lista de ficheros). Ni documentos base ni especificación: si la huella cambiara, la decisión
  sería ROTATE. `state` muestra la última decisión y el motivo de la última rotación.
- Código **10 = ROTATE**: imprime el motivo exacto (modelo, dominio, huella, revisión
  independiente, tareas o turnos acumulados, contexto por encima del tope, edad o economía) y
  obliga a handoff de diez líneas. Si el motivo es «no hay sesión registrada», se abre sesión y
  se repite: sin historial no hay handoff.
- Código **2 = error de uso**: el rol no está en `registry.json`, la especificación o el packet
  no existen o el packet es de otra tarea. No es una decisión: se corrige y se vuelve a preguntar.

La huella de entrada se calcula sobre card, rol, especificación y contrato de entrega: si
cualquiera cambia, la sesión está desactualizada y no hay discusión posible.

## 7c. Memoria del rol

Cada rol mantiene `memory/<rol>.yaml` (plantilla `templates/memory.yaml`). Se lee al abrir sesión
y se reescribe al cerrar, con poda. El presupuesto es una página: `current_state` tres líneas,
`decisions` y `traps` cinco como máximo, `handoff` diez. Lo que ya está en la especificación, el
ADR o el tablero se referencia, no se copia; si algo importa más allá de este rol o de esta
sesión, va al ADR o a la especificación.

`session.py close --memory memory/<rol>.yaml` avisa si el fichero falta, si no se actualizó desde
que abrió la sesión o si pasa de 8 192 B. Una rotación con la memoria vacía es una rotación que
pierde el porqué.

## 8. Cuando algo se rompe

| Situación | Quién actúa |
|---|---|
| Dos roles escriben lo mismo | Para el segundo; resuelve el dueño |
| Falta una dependencia | `blocked` en el tablero; no se improvisa |
| La especificación no responde una duda | `spec-owner` la cierra con quien pidió la funcionalidad |
| Un test falla | Ticket si es defecto; si es inestabilidad, `qa-automation` |
| Se incumple una decisión de arquitectura | `software-architect` bloquea y exige decisión escrita |
| Un defecto llega a producción | Ticket con severidad, corrección revisada y nota pública |
| Un agente se atasca | Devuelve control con el motivo; no amplía alcance |

## 8b. Tickets

Un defecto se guarda en `tickets/<id>.yaml` (plantilla `templates/ticket.yaml`). Lo abre
cualquiera que lo detecte, con pasos, esperado, actual y evidencia. Lo prioriza `qa-lead` por
severidad y lo cierra `qa-lead` tras verificar en el entorno afectado: nunca quien lo escribió
ni quien lo arregló.

`orchestrator` decide la ola: un **S1** (pérdida de datos, seguridad o caída) preempta y entra
en la ola en curso o en la siguiente; un **S2** va en la siguiente; **S3** y **S4** esperan a una
ola de mantenimiento. Entrar al tablero significa una tarea con rama `fix/<ticket>-slug` y la
especificación que cita el ticket.

Dos reglas que no se negocian: un defecto no se arregla dentro de otra tarea, y si revela que la
especificación está mal, no es un ticket: es un cambio de especificación de `spec-owner`.

## 9. Señales de que va bien

- Cada funcionalidad en curso tiene especificación aprobada.
- Cada tarea tiene dueño, packet y criterio verificable.
- Ningún PR se cierra con una firma requerida vacía.
- Los roles respetan la matriz de permisos.
- Cada rol tiene su memoria al día y podada.
- Cada ola deja un handoff y el estado no depende del historial de nadie.
- Una persona ajena al día a día entiende el estado leyendo el tablero.
