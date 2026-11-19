# Plantillas

Todo lo que se rellena al instanciar. Ninguna plantilla pertenece a un producto concreto.

## Para arrancar un producto

| Plantilla | Formato | Quién la rellena | Para qué |
|---|---|---|---|
| [`interview.yaml`](interview.yaml) | YAML | `discovery-lead`, `spec-owner` | baterías de preguntas |
| [`product-brief.md`](product-brief.md) | MD | `discovery-lead` | el «qué» del producto |
| [`roster.yaml`](roster.yaml) | YAML | `discovery-lead` | qué expertos se instancian y cuáles faltan |
| [`domain.yaml`](domain.yaml) | YAML | `discovery-lead` + `software-architect` | capacidades funcionales y sus dueños |
| [`spec.yaml`](spec.yaml) | YAML | `spec-owner` | una especificación por funcionalidad |
| [`adr.yaml`](adr.yaml) | YAML | `software-architect` | decisiones que condicionan todo |

## Para ejecutar el trabajo

| Plantilla | Formato | Quién la rellena |
|---|---|---|
| [`feature.yaml`](feature.yaml) | YAML | tech leads |
| [`packet.json`](packet.json) | JSON | `orchestrator` |
| [`backlog.json`](backlog.json) | JSON | `orchestrator` |
| [`ticket.yaml`](ticket.yaml) | YAML | quien detecta el defecto |
| [`memory.yaml`](memory.yaml) | YAML | cada rol, sobre sí mismo |
| [`sessions.json`](sessions.json) | JSON | `orchestrator` — reutilizar o rotar sesión |
| [`pull-request.md`](pull-request.md) | MD | el autor del PR |

## Para ampliar el equipo

| Plantilla | Formato | Nota |
|---|---|---|
| [`role.yaml`](role.yaml) | YAML | crear un rol obliga a tocar rol, registro y permisos; la card se genera |
| [`card.yaml`](card.yaml) | YAML | forma del bloque `card:` dentro del rol; la card no se escribe a mano |

## Criterio de calidad de una plantilla rellenada

- Se entiende sin hablar con quien la escribió.
- No contiene «pendiente» sin dueño ni fecha.
- Cada afirmación verificable dice cómo se comprueba.
- No repite lo que ya dice un rol: lo referencia.
