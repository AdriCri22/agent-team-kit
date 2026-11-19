# Modelo operativo

Cómo se organiza y se coordina el equipo. Documento para personas: los agentes reciben su
parte ya resuelta en su card y en el packet de la tarea.

## Jerarquía

Ocho capas. La fuente para herramientas es [`registry.json`](registry.json); esta tabla es
la misma información para leer de un vistazo.

```text
                       quien encarga el producto
                                  │
   ┌──────────────┬───────────────┼───────────────┬──────────────┐
   │              │               │               │              │
org-architect  discovery-lead  product-owner  software-architect
orchestrator                      │                │
                    ┌──────────────┼─────────┐      ├── tech-lead-backend ──┬── backend-engineer
                    │              │         │      │                       ├── database-engineer
              spec-owner     ux ─ ui     qa-lead   │                       └── domain-owner
                    │              │         │      ├── tech-lead-frontend ─── frontend-engineer
                    │              │    qa-automation
                    │              │
              content-writer  communications-lead  growth-analyst

   software-architect ── devops-engineer
                      └─ security-engineer
```

| Capa | Rol | Reporta a | Decide sobre |
|---|---|---|---|
| Dirección | `orchestrator` | quien encarga | orden, olas, reparto, packets, sesiones |
| Gobierno | `org-architect` | quien encarga | qué roles existen, sus límites y sus permisos |
| Gobierno | `structure-steward` | software-architect | coherencia entre la estructura real y el mapa |
| Gobierno | `legal-officer` | quien encarga | obligaciones legales, textos, licencias y compromisos |
| Descubrimiento | `discovery-lead` | quien encarga | necesidades, equipo propuesto, dominios |
| Definición | `product-owner` | quien encarga | valor, alcance, aceptación |
| Definición | `spec-owner` | product-owner | especificación de cada funcionalidad |
| Definición | `ux-designer` | product-owner | flujos, estados, arquitectura de información |
| Definición | `ui-designer` | ux-designer | sistema de diseño, componentes, vista |
| Ingeniería | `software-architect` | quien encarga | estructura, límites, contratos |
| Ingeniería | `tech-lead-backend` | software-architect | datos, servidor, revisión backend |
| Ingeniería | `tech-lead-frontend` | software-architect | interfaz, revisión frontend |
| Ingeniería | `backend-engineer` | tech-lead-backend | implementación de servidor |
| Ingeniería | `frontend-engineer` | tech-lead-frontend | implementación de interfaz |
| Ingeniería | `database-engineer` | tech-lead-backend | esquema, migraciones, integridad |
| Ingeniería | `performance-engineer` | software-architect | presupuestos de rendimiento y capacidad |
| Ingeniería | `payments-engineer` | tech-lead-backend | dinero: representación, cobro y conciliación |
| Ingeniería | `mobile-engineer` | tech-lead-frontend | cliente móvil, sin red y ciclo de vida |
| Ingeniería | `ml-engineer` | tech-lead-backend | datos, modelos, evaluación y deriva |
| Ingeniería | `domain-owner` | tech lead de su disciplina | una capacidad funcional completa |
| Aseguramiento | `qa-lead` | product-owner | riesgo, cobertura, severidad, verificación |
| Aseguramiento | `qa-automation` | qa-lead | automatización de pruebas e2e |
| Aseguramiento | `security-engineer` | software-architect | amenazas, permisos, datos personales |
| Operación | `devops-engineer` | software-architect | entornos, despliegue, observabilidad |
| Comunicación | `content-writer` | product-owner | textos, tono, idiomas |
| Comunicación | `technical-writer` | product-owner | guías y referencia para quien usa o integra |
| Comunicación | `communications-lead` | product-owner | notas de entrega y mensaje público |
| Comunicación | `growth-analyst` | product-owner | medición, experimentos, adopción |

**No hay jefe único.** Cada capa tiene su dominio de decisión y no invade el de otra. Un
rol solo escala cuando la decisión afecta a otro dominio, y escala a su `reports_to`.

## Un archivo, un dueño

- La propiedad se declara en el mapa de dominios y en `shared-write-set.json`.
- Nadie escribe en superficie ajena: se pide al dueño.
- Las superficies transversales requieren revisión de tech lead y, si tocan datos,
  exposición o infraestructura, de `security-engineer` o `devops-engineer`.
- Las excepciones van a `ownership-exceptions.json` con motivo y caducidad. Sin caducidad
  es deuda, no regla.

## Quién lee qué

[`permissions.json`](permissions.json) es la fuente única. Tres reglas que resumen el
criterio:

1. **La especificación es de todos.** Brief, especificación de funcionalidad y decisiones
   los lee cualquier rol: de ahí salen implementación, pruebas y vista.
2. **Quien decide el qué no lee código.** `product-owner`, `ux-designer` y `ui-designer`
   trabajan con especificación y evidencia, nunca con la implementación.
3. **La calidad es independiente.** `qa-lead` y `qa-automation` solo ven pruebas e2e y
   resultados; nunca código de producto ni pruebas unitarias.

El packet de cada tarea materializa la matriz en rutas concretas: `allowed`, `forbidden`.

## Conocimientos y escalado

Cada rol declara dos cosas más, porque un límite sin salida se convierte en una invención:

- **`knowledge`**: el juicio que se le supone para decidir, no la lista de herramientas.
- **`escalates_to`**: a quién pregunta cuando algo se sale de su alcance.

Y una regla común, en [`policy/delivery.yaml`](policy/delivery.yaml#when_unsure): **si no
sabes algo, no lo inventes; pregunta**. La tabla por tipo de duda es la misma para todos:
comportamiento → `spec-owner`; alcance o prioridad → `product-owner`; estructura →
`software-architect`; implementación → tu tech lead; datos, permisos o abuso →
`security-engineer`; entorno → `devops-engineer`; tus propios límites → tu `escalates_to`.

Una pregunta concreta con la opción que crees correcta es trabajo; «¿cómo lo hago?» no lo
es. Mientras esperas respuesta, no implementes la suposición: marca la tarea bloqueada.

## La especificación manda

Por cada funcionalidad existe **un** documento en `specs/`, escrito por `spec-owner` con
entrevista al usuario:

- de él salen las tareas de ingeniería,
- de él salen los casos de prueba de `qa-lead`,
- de él sale la vista de `ux-designer` y `ui-designer`,
- de él sale el texto de `content-writer`.

Si el código y la especificación discrepan, gana la especificación hasta que se cambie con
decisión escrita. Una funcionalidad sin especificación no entra en una ola.

## El viaje de una funcionalidad

1. `product-owner` fija intención, valor y prioridad.
2. `spec-owner` entrevista y escribe la especificación; `ux-designer` añade flujos y
   estados; `ui-designer` concreta la vista.
3. `software-architect` marca restricciones y contratos afectados.
4. Los tech leads convierten la especificación en tareas con criterios verificables.
5. `orchestrator` compone la ola, crea ramas y prepara packets.
6. Los ingenieros implementan; `database-engineer` cuida el esquema.
7. `qa-lead` verifica contra los criterios; `qa-automation` deja la regresión.
8. `content-writer` cierra textos; `communications-lead` publica la nota de entrega.
9. `product-owner` acepta. `orchestrator` cierra y deja el relevo de la ola en
   `.orchestrator/handoff.md`.

Nadie revisa, verifica y aprueba su propio trabajo.

## Olas

- Una tarea solo entra si sus dependencias están cerradas y no solapa superficies.
- Cada ola deja el sistema coherente si se detiene el trabajo.
- Empieza con dos o tres workers de roles distintos, no con veinte: el registro mantiene una
  sesión abierta por rol, así que dos tareas del mismo rol van en olas distintas.
- Dos tareas que escriben el mismo archivo van en olas distintas, por orden de dueño.

## Sesiones y contexto

La política es **ejecutable**, no una recomendación: la decide
[`tools/session.py`](tools/session.py) comparando estado observable.

```bash
python3 agents/tools/session.py decide --role <rol> --spec <ruta> \
  --model <modelo> --domain <dominio> --packet <packet.json>
# 0 = REUSE, 10 = ROTATE, con el motivo y la comparación de coste
```

Dos reglas, en orden: **legalidad** (huella de documentos, modelo, dominio, auditoría
independiente, contadores, tope de contexto) y **economía** (¿compensa reutilizar para la tarea
que viene?).

- **Reutiliza por defecto** cuando ambas pasan: el historial ya está pagado. `--continuation`
  o `--follow-up` ya no son permisos, solo dejan escrito a qué hilo pertenece la tarea.
- **Rota** con un motivo que imprime la herramienta: cambió un documento, el modelo, el dominio,
  se agotó la ventana, la sesión es vieja o reutilizar no compensa. La rotación exige handoff de
  diez líneas; la sesión nueva arranca con card, memoria y tarea.
- **El presupuesto se deriva, no se escribe**: `open --window N` declara la ventana del modelo y
  de ella salen los bytes cargables, los turnos y las tareas que caben. Los techos del perfil
  acotan la coherencia, no la capacidad: con 1 M de contexto manda el coste, no el espacio.
- **Los turnos se acumulan**: `close` guarda el total de la sesión y el journal manda sobre lo
  declarado, así que el tope de turnos se alcanza de verdad.
- **Al reutilizar** se envía solo el delta que imprime la herramienta, con el ancla de la huella.
- **Contra la alucinación**: si cambió un documento, la huella lo detecta y obliga a rotar; si el
  worker improvisa o la especificación no dice lo que la sesión recuerda, se rota aunque quede
  presupuesto.

La calibración de coste —caché de prefijo, su descuento y su caducidad— **no está en la
política**: vive en [`policy/providers.yaml`](policy/providers.yaml) como perfiles
sustituibles. Cambiar de proveedor es cambiar de perfil; la decisión sigue siendo la misma.

### De dónde salen los números

No se le piden a ningún editor ni a ningún modelo: cada uno expone cosas distintas, o nada, y sus
cifras no son comparables. Se usan cuatro fuentes que existen siempre
([`policy/measurement.yaml`](policy/measurement.yaml)):

| Fuente | Qué aporta | Cómo se obtiene |
|---|---|---|
| Declarada | turnos, lecturas y escrituras | `session.py record --event turn\|read\|write` (una llamada por hito) |
| Derivada | ficheros y bytes cambiados | `git diff` del trabajo |
| Calculada | contexto cargado | documentos reales + turnos × coste por turno + packet |
| Declarada por el producto | ventana, coste por turno, techos | `providers.yaml`, con los cálculos de `#limits` |

`session.py verify --packet <ficha> [--branch <rama>]` compara el diff real de la rama del PR
con el write set: lo que no está en el conjunto declarado es contaminación, y un diff vacío es un
error, no una tarea limpia. Revisa también lo que está sin confirmar.

La memoria del rol sustituye al historial. `orchestrator` registra cada decisión con
`session.py decide`, que la deja en `sessions.jsonl` y actualiza `sessions.json` con
`open|close`.

El estado vive junto a `agents/` (`sessions.json`, `sessions.jsonl` y su cerrojo), así que da
igual desde qué directorio se ejecute, y el registro se serializa entre procesos: dos cierres
simultáneos no se pisan. El relevo del orquestador es un fichero, no un recuerdo: diez líneas en
`.orchestrator/handoff.md` al cerrar cada ola.

La memoria del rol vive en `memory/<rol>.yaml`: una página, se reescribe al cerrar y se poda. Lo
que ya está en la especificación, el ADR o el tablero se referencia, no se copia. `close
--memory` avisa si falta, si está vieja o si pasa del tope.

## Herramientas

| Comando | Qué comprueba | Códigos |
|---|---|---|
| `session.py check --packet` | estructura del packet y categorías contra `permissions.json` | 0/1/2 |
| `session.py decide` | legalidad y economía de la sesión | 0 REUSE · 10 ROTATE · 2 uso |
| `session.py close --memory --board` | turnos acumulados, memoria del rol y coherencia con el tablero | 0/10 |
| `session.py verify --branch` | permisos del packet y diff de la rama contra el write set | 0/1/2 |
| `board.py lint` | estados, dependencias, firmas, PR, packets (rol, especificación y permisos) y solapes | 0/1/2 |
| `validate.py` | formato, coherencia, permisos, cards y referencias del sistema | 0/1 |
| `validate.py --product` | sistema y tablero de una instancia del producto | 0/1/2 |
| `build-cards.py` | genera las cards desde los roles (`--check` en CI) | 0/1 |

La comprobación de permisos vive en `agents/tools/authz.py` y la usan las tres puertas: admisión,
merge y tablero. Una sola verdad, un solo cambio cuando la matriz cambia.

## Tickets

Un defecto es un ticket con severidad, pasos, esperado, actual y evidencia. Se guarda en
`tickets/<id>.yaml`, se prioriza por severidad (`qa-lead`) y `orchestrator` lo mete en ola: S1
preempta, S2 va en la siguiente, S3 y S4 esperan a mantenimiento. Lo cierra `qa-lead` tras
verificar, nunca quien lo escribió.

## Formatos

| Audiencia | Formato |
|---|---|
| Solo personas | `.md` |
| Personas y agentes | `.yaml` |
| Solo máquinas | `.json` |

La card de cada rol **se genera** de su bloque `card:` con
`python3 agents/tools/build-cards.py`; el validador falla si alguien la edita a mano.

## Roles condicionales

Existen en el catálogo pero solo se instancian cuando el producto los necesita. Crearlos sin
necesidad observada es burocracia; `discovery-lead` propone y `org-architect` decide con su
`split_test`.

| Se instancia cuando… | Rol |
|---|---|
| Hay que prometer latencia, capacidad o coste por uso | `performance-engineer` |
| El producto cobra, factura o devuelve dinero | `payments-engineer` |
| Hay cliente móvil o nativo con ciclo propio | `mobile-engineer` |
| Parte del producto es un modelo o datos aprendidos | `ml-engineer` |
| Alguien de fuera tiene que usar o integrar el producto | `technical-writer` |
| La estructura real empieza a no coincidir con el mapa | `structure-steward` |
| Hay usuarios, contratos, textos legales, licencias o cobros | `legal-officer` |

## Señales de instanciación enferma

- Los roles empiezan a nombrar módulos del producto.
- Hay funcionalidades sin especificación.
- Un rol lee código que su fila de `permissions.json` le prohíbe.
- El tablero lo edita alguien que no es `orchestrator`.
- Los criterios de aceptación son adjetivos.
- Nadie sabe quién firma la aceptación.

El arreglo está en el brief, en la especificación o en las tareas; nunca en los roles.
