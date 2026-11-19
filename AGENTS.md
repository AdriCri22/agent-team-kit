# AGENTS.md — reglas para quien trabaja en este repositorio

Este repositorio contiene **el sistema de agentes**, no un producto.

## Si vienes a usar el equipo en un producto

1. Lee `README.md` y `agents/START-HERE.md`.
2. Empieza por `discovery-lead`: entrevista, propuesta de equipo y brief.
3. Una **especificación por funcionalidad** en `specs/`, escrita con `spec-owner`.
4. Tareas y olas con `templates/feature.yaml` y `agents/prompts/orchestrator.yaml`.

## Si vienes a tocar los roles

Cada `agents/roles/<rol>.yaml` describe una **capacidad permanente**, no un proyecto:

- No nombres productos, módulos, tecnologías ni empresas.
- No asignes tareas ni identificadores: una tarea caduca, una capacidad no.
- No fijes rutas reales: describe la propiedad en abstracto y deja que la instanciación
  la traduzca.
- No des por sentadas convenciones (idioma, moneda, zona horaria): se refieren a «las
  convenciones declaradas en el brief».
- Si cambias un rol, actualiza su card, `agents/registry.json` y
  `agents/permissions.json`. Los tres deben decir lo mismo.
- **La card no se edita a mano**: se genera del bloque `card:` del rol con
  `python3 agents/tools/build-cards.py`. Si cambias la card, cambias el rol y regeneras.
- Antes de abrir el PR, ejecuta `python3 agents/tools/validate.py`: valida formato,
  coherencia entre las cuatro fuentes, jerarquía sin ciclos, reglas de permisos, cards al día
  y enlaces.
- Un rol nuevo lo diseña `org-architect`, no cualquiera con una idea: hay que justificar por
  qué no basta con dividir o ampliar un rol existente (ver su `split_test`).
- Los ítems de lista en YAML van entrecomillados. Un texto con `: ` sin comillas se
  interpreta como mapa y el fichero miente sin dar error.

## Formatos (no negociable)

| Audiencia | Formato |
|---|---|
| Solo personas | `.md` |
| Personas y agentes | `.yaml` |
| Solo máquinas | `.json` |

Un fichero nuevo se crea en el formato de su audiencia, no por costumbre.

## Reglas del sistema

- Un archivo tiene dueño declarado antes de escribirse.
- Una tarea, una rama, un PR; nunca trabajo directo sobre la rama principal.
- Ningún rol edita el tablero de planificación: lo hace `orchestrator`.
- Ningún rol cierra su propio trabajo: firman revisión técnica, calidad y producto.
- Los defectos se registran como tickets, no se arreglan dentro de otra tarea.
- **La especificación manda**: si el código y la especificación discrepan, gana la
  especificación hasta que se cambie con decisión escrita.
- Las reglas de dinero, seguridad, permisos, fechas y obligaciones legales se citan
  literalmente.

## Prohibiciones

- No añadir código, dependencias ni configuración de un stack concreto.
- No añadir secretos, datos personales ni credenciales.
- No convertir la plantilla en un proyecto: si necesitas producto, otra rama u otro
  repositorio.
