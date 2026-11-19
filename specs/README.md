# Especificaciones de funcionalidad

Una especificación por funcionalidad. Es el documento **que lee todo el equipo**: de aquí
salen la implementación, las pruebas y la vista.

## Reglas

1. Una funcionalidad, un fichero: `specs/<feature>.spec.yaml`.
2. La escribe `spec-owner` entrevistando a quien la pide, con `templates/spec.yaml`.
3. Se aprueba antes de que existan tareas. Una funcionalidad sin especificación aprobada no
   entra en una ola.
4. **Manda sobre el código.** Si el código y la especificación discrepan, gana la
   especificación hasta que se cambie con decisión escrita.
5. Los cambios se hacen en la especificación primero y luego se propagan a tareas, pruebas
   y vista. Nunca al revés.
6. Lo que no está claro va en `open_questions`; una especificación con preguntas abiertas no
   se aprueba.

## Quién lee qué de la especificación

| Rol | Qué saca de ella |
|---|---|
| `tech-lead-backend`, `tech-lead-frontend` | tareas, contratos, criterios verificables |
| `backend-engineer`, `frontend-engineer`, `domain-owner` | comportamiento exacto y límites |
| `database-engineer` | datos que se leen y se escriben, invariantes |
| `ux-designer`, `ui-designer` | flujos, estados y requisitos de la vista |
| `qa-lead`, `qa-automation` | casos, oráculos y criterio de aceptación |
| `content-writer` | textos, tono e idiomas |
| `security-engineer` | datos personales, permisos y abusos posibles |
| `growth-analyst` | eventos y métricas implicadas |
| `product-owner` | valor y aceptación |

## Estados

| Estado | Significado |
|---|---|
| `draft` | en redacción, con preguntas abiertas |
| `review` | lista para que el equipo la lea y objete |
| `approved` | se puede partir en tareas |
| `building` | hay tareas en curso |
| `released` | entregada y aceptada |
| `superseded` | sustituida por otra especificación |
