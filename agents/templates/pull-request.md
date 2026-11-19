# Plantilla de pull request

Documento para personas: se pega en el cuerpo del PR. Un PR que no se puede revisar sin
abrir el código está mal descrito.

```md
## <tarea> — <título>

**Rol:** `<rol>` · **Especificación:** `specs/<feature>.spec.yaml`
**Firmas requeridas:** <todas las de `signatures_required`, ninguna del autor>

## Qué cambia

<Dos o tres frases.>

## Qué no cambia

<Lo que alguien podría suponer y no cambia.>

## Cómo verificarlo

```bash
<comando>
```

Resultado: <en verde / detalle>. Evidencia: <artefacto o salida>.

## Criterios de aceptación

| Criterio | Evidencia |
|---|---|
| `AC-1` | <prueba o comprobación> |

## Riesgos y deuda

- <riesgo asumido y qué lo compensa>
- <deuda declarada y condición de salida>

## Checklist

- [ ] Gate de tipos o compilación en verde.
- [ ] Pruebas nuevas o actualizadas.
- [ ] Sin secretos ni datos personales en el diff, los logs o la salida de pruebas.
- [ ] Contratos actualizados y consumidores avisados.
- [ ] Migración reversible y probada (si toca esquema).
- [ ] Accesibilidad revisada (si toca interfaz).
- [ ] Textos fuera del código (si toca interfaz).
- [ ] Firmas requeridas completas y ninguna del autor del trabajo.
- [ ] Memoria del rol actualizada.

## Handoff

```json
{
  "task": "<tarea>",
  "status": "done",
  "branch": "<rama>",
  "pr": "<url>",
  "evidence": "<qué se ejecutó y qué se vio>",
  "notes": "<decisiones y deuda>"
}
```
```
