# Tests de las herramientas

Verifican el contrato de [`agents/tools/session.py`](../agents/tools/session.py),
[`board.py`](../agents/tools/board.py) y [`validate.py`](../agents/tools/validate.py): códigos
0/1/2/10, topes derivados de la ventana, huella de los documentos, economía, permisos del packet,
coherencia del tablero y las reglas del propio validador.

**No son herramientas de rol.** Ningún worker las ejecuta durante una tarea y no forman parte de
lo que se entrega al producto: son la red de seguridad de quien cambia el sistema de agentes.

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

Cada caso trabaja en un directorio temporal con su propio registro, journal, especificación y
tablero: nunca tocan el `sessions.json` real. Los tests de `validate.py` copian `agents/` a un
temporal y rompen una regla a propósito; nunca mutan el árbol real.

`test_pipeline.py` es la prueba de costuras: copia `agents/` dentro de un producto temporal y
recorre tablero → packet → check → open → decide → record → close → verify → board lint. Si una
ruta, un nombre de campo o una convención deja de coincidir entre herramientas, falla ahí.

En la matriz de permisos esta carpeta es `system-tests`: solo `org-architect` y
`structure-steward` la escriben, y `orchestrator` y `devops-engineer` la leen. Ningún worker la
recibe en su packet.

## Reglas al añadir un test

- **Caja negra**: se lanza el CLI con `subprocess`, no se importa su lógica para simularla.
- **Expectativas desde la política**: los números se leen de `agents/policy/providers.yaml` o se
  comprueban como relaciones (subir la ventana nunca reduce un tope), no se copian a mano.
- **Sin fixtures en el árbol**: cualquier YAML dentro del repo lo valida `validate.py` como rol o
  política. Todo se genera en `tempfile`.
