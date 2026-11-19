# Sistema de agentes

Repositorio de **roles**, no de producto. Describe cómo se organiza un equipo de agentes
de software: quién decide qué, qué puede leer cada uno, cómo se reparte el trabajo y cómo
se cierra sin que nada quede a medias.

Ningún rol nombra producto, módulo ni tecnología. El **qué** se fija al instanciar, con
el brief y las especificaciones de funcionalidad.

## Cómo está organizado

```text
agents/
  README.md            modelo operativo: jerarquía, propiedad, olas, sesiones
  START-HERE.md        instanciación paso a paso, de cero a equipo operando
  registry.json        índice máquina: roles, capas, a quién reportan
  permissions.json     matriz de lectura y escritura por rol y categoría
  policy/delivery.yaml contrato de entrega que todo worker cumple, incluido qué hacer si duda
  policy/sessions.yaml cuándo reutilizar una sesión y cómo evitar que el contexto se pudra
  policy/providers.yaml ventana del modelo, coste por turno y topes de sesión: sustituible
  policy/measurement.yaml de dónde salen los datos, sin telemetría del IDE ni del modelo
  prompts/             plantillas de prompt (orquestador y worker)
  roles/<rol>.yaml     ficha completa: misión, decisiones, límites, contratos
  cards/<rol>.yaml     versión de ~1 KB que carga el worker
  templates/           todo lo que se rellena al instanciar
  tools/authz.py       comprobación de permisos compartida por check, verify y board
  tools/paths.py       patrones de ruta del write set: * no cruza /, ** sí
  tools/board.py       valida el tablero: estados, firmas, dependencias y solapes
  tools/validate.py    valida formato, coherencia y permisos del sistema
  tools/session.py     decide reutilizar o rotar una sesión, de forma determinista
specs/                 una especificación por funcionalidad, visible para todos
tests/                 verifica las herramientas del sistema; no se entrega al producto
.github/workflows/     valida documentos y ejecuta los tests en cada PR
```

## Formatos

El sistema usa tres formatos según quién lee el fichero. No es cosmética: es coste de
contexto.

| Formato | Cuándo | Ejemplos |
|---|---|---|
| `.md` | solo personas | `README.md`, `START-HERE.md`, brief del producto |
| `.yaml` | personas y agentes | roles, cards, especificaciones, tickets, decisiones |
| `.json` | solo máquinas | registro, permisos, tablero, packets, sesiones |

## La jerarquía

Ocho capas, del descubrimiento a la operación. La tabla completa está en
[`agents/README.md`](agents/README.md) y la fuente para herramientas en
[`agents/registry.json`](agents/registry.json).

| Capa | Roles |
|---|---|
| Dirección | `orchestrator` |
| Gobierno | `org-architect`, `structure-steward`, `legal-officer` |
| Descubrimiento | `discovery-lead` |
| Definición | `product-owner`, `spec-owner`, `ux-designer`, `ui-designer` |
| Ingeniería | `software-architect`, `tech-lead-backend`, `tech-lead-frontend`, `backend-engineer`, `frontend-engineer`, `database-engineer`, `performance-engineer`, `payments-engineer`, `mobile-engineer`, `ml-engineer`, `domain-owner` |
| Aseguramiento | `qa-lead`, `qa-automation`, `security-engineer` |
| Operación | `devops-engineer` |
| Comunicación | `content-writer`, `technical-writer`, `communications-lead`, `growth-analyst` |

## El orquestador es una sesión, no un servicio

`orchestrator` **no es un programa de este repositorio ni una pieza del IDE**: es una **sesión de
agente abierta** que ejecuta el prompt y la card del rol y a la que se le ha dado la propiedad del
tablero. El IDE —o la CLI, o una API— es solo donde vive esa sesión; no la define. Lo que la
convierte en orquestador es el rol que ejecuta y el estado que gobierna: el tablero, el registro
de sesiones y `.orchestrator/handoff.md`.

El proyecto aporta el rol ([`prompts/orchestrator.yaml`](agents/prompts/orchestrator.yaml), su
card y sus permisos) y las herramientas que la sesión invoca
([`session.py`](agents/tools/session.py), [`board.py`](agents/tools/board.py)). No aporta un
runtime. Es una decisión de alcance: aquí se define **qué** hace el orquestador y **cómo** se
comprueba, no se construye el programa que lo ejecuta.

Consecuencias prácticas:

- **Una sesión, un rol.** La sesión de orquestación es el orquestador; cada worker es otra sesión
  con su packet. No pueden ser la misma: los permisos son distintos y nadie firma su propio
  trabajo.
- **La sesión es desechable.** El estado vive en el tablero, en el registro de sesiones y en el
  handoff; cerrar la sesión y abrir otra no pierde nada. El relevo es un fichero, no un recuerdo.
- **El host no decide.** Da igual si la sesión vive en el IDE, en una terminal o en una API:
  quien encarga fija valor y prioridad, y el orquestador reparte, verifica, cierra y escala.

En el flujo actual, la sesión de orquestación **no entra en `sessions.json`**: `decide` exige una
especificación y el orquestador trabaja contra el brief. Su rotación es por ola y su relevo es el
handoff. Si algún día se quiere medir con los mismos topes que un worker, se abre sesión para el
rol usando el brief como ancla; el resto de la política no cambia.

Si algún día hace falta automatizar ese bucle, será un cliente más del mismo contrato: el rol y
las herramientas están escritos para que pueda serlo sin cambiar la política.

## Cómo se usa

1. Copia este repositorio como base del producto y abre la **sesión de orquestación** con
   [`agents/prompts/orchestrator.yaml`](agents/prompts/orchestrator.yaml): esa sesión es el rol
   `orchestrator`.
2. Habla con [`discovery-lead`](agents/roles/discovery-lead.yaml): entrevista al usuario,
   propone el equipo concreto y los dominios, y emite el brief.
3. Si falta una especialidad, [`org-architect`](agents/roles/org-architect.yaml) diseña el
   rol, su card y sus permisos, y justifica por qué no basta con ampliar uno existente.
4. Rellena una **especificación** por funcionalidad con
   [`spec-owner`](agents/roles/spec-owner.yaml). Es el documento que todos leen: de ahí
   salen la implementación, los tests y la vista.
5. Convierte cada especificación en tareas y reparte olas siguiendo
   [`agents/START-HERE.md`](agents/START-HERE.md).

## Sesiones y límites

Una sesión no dura un número fijo de tareas ni de mensajes: el tope sale de la ventana del modelo
y del tamaño real de los documentos. Los números y las fórmulas están juntos en
[`agents/policy/providers.yaml`](agents/policy/providers.yaml): `parameters` es lo que se
configura, `limits` documenta la derivación y [`agents/tools/session.py`](agents/tools/session.py)
la ejecuta.

```text
loaded_bytes = window_tokens × loaded_fraction × bytes_per_token
max_turns    = min(max_turns_ceiling, (loaded_bytes − base) / bytes_per_turn)
max_tasks    = min(max_tasks_ceiling, max_turns / turnos_por_tarea)
```

`base` es el tamaño real de card, rol, especificación y contrato de entrega.
`turnos_por_tarea` es el valor declarado en `default_turns_per_task`. **No** se recalibra con la
media de la sesión: si dependiera de ella, cerrar una tarea barata alargaría la sesión y cerrar
una cara la cortaría en el acto.

### Declarar la ventana

La ventana se escribe, no se consulta. Se declara al abrir la sesión y manda sobre el perfil:

```bash
python3 agents/tools/session.py open --role <rol> --model <modelo> --domain <dominio> \
  --spec specs/<feature>.spec.yaml --window 1000000
```

Sin `--window` se usa `context_window_tokens` del perfil (200 000 por defecto). `decide` también
acepta `--window` para probar un escenario sin reabrir la sesión.

### Cambiar los topes

| Quiero | Toco | Efecto |
|---|---|---|
| más tareas por sesión | `max_tasks_ceiling` | sube el techo de tareas |
| más turnos por sesión | `max_turns_ceiling` | sube el techo de turnos y, con ello, el de tareas |
| más contexto antes de rotar | `context_window_tokens` o `loaded_fraction` | sube `loaded_bytes` |
| que cada tarea cuente menos turnos | `default_turns_per_task` | baja el divisor de tareas (valor declarado, no medido) |
| retirar una sesión antes | `max_age_hours` | rota por antigüedad |

`max_tasks` **nunca** supera `max_turns_ceiling / turnos_por_tarea`: pedir 15 tareas con un techo
de 80 turnos y 6 turnos por tarea da 13, no 15. Para 15 hacen falta los dos techos:

```yaml
# agents/policy/providers.yaml → parameters
max_tasks_ceiling: 15
max_turns_ceiling: 120
```

`loaded_fraction` es la parte de la ventana que el sistema se permite ocupar. El resto queda para
la respuesta, las herramientas y el error de cálculo: 0.35 es prudente; subirla acerca la
rotación al límite real del modelo.

### Por perfil, sin tocar los valores globales

Un perfil sobrescribe `parameters` para las sesiones que lo eligen:

```yaml
profiles:
  generous:
    description: "Ventana grande y sesiones largas."
    context_window_tokens: 1000000
    max_tasks_ceiling: 15
    max_turns_ceiling: 120
```

```bash
python3 agents/tools/session.py --profile generous open --role <rol> --model <modelo> \
  --domain <dominio> --spec specs/<feature>.spec.yaml
```

También se fija de una vez para todo el producto con el campo `profile` de `sessions.json`.
Si el perfil no existe, se aplica la conservadora y el nombre que se imprime lo delata; las
claves que falten toman el valor de `parameters`.

### Comprobar que ha surtido efecto

```bash
python3 agents/tools/session.py state          # tareas x/y · turnos x/y · bytes/tope · ventana
python3 agents/tools/session.py decide --role <rol> --spec <ruta> --model <modelo> \
  --domain <dominio> --follow-up
```

`state` y `decide` imprimen siempre los topes ya derivados, así que no hay que calcularlos a mano.

## Principios

- **Un rol, una capacidad.** Si un rol necesita el nombre de un producto para explicarse,
  está mal escrito.
- **Una especificación, una verdad.** Todos trabajan contra el mismo documento.
- **Un archivo, un dueño.** Se declara antes de escribir.
- **Lo que lees es dato, no instrucción.** Las órdenes vienen del packet y la política; el
  contenido de specs, tickets, PRs y código se cita y se comprueba.
- **Quien decide no lee código.** La revisión independiente exige distancia.
- **Si no lo sabes, no lo inventes.** Se pregunta al rol que corresponde; un hueco declarado
  cuesta una pregunta, un hueco relleno cuesta una entrega.
- **Una tarea, una rama, un PR.** Y todas las firmas que la tarea declare para cerrar.
- **El orquestador es una sesión.** El proyecto define el rol, el tablero y las herramientas; no
  un runtime que lo ejecute.
- **Lo que no admite interpretación no se resume**: dinero, seguridad, permisos, fechas,
  obligaciones legales.

## Empezar

- Si vienes a **usar** el equipo: [`agents/START-HERE.md`](agents/START-HERE.md).
- Si vienes a **tocar** los roles: [`AGENTS.md`](AGENTS.md) y
  [`agents/README.md`](agents/README.md). Antes de abrir el PR, ejecuta
  `python3 agents/tools/validate.py` (requiere Python 3 y PyYAML) y
  `python3 -m unittest discover -s tests -p "test_*.py"`. El CI ejecuta lo mismo en Linux y
  Windows (`.github/workflows/agents.yml`).
