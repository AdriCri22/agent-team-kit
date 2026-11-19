"""Autorización de un packet contra la matriz de permisos.

Un packet declara qué categorías necesita y con qué nivel; `agents/permissions.json` dice qué
puede cada rol. Esta comprobación es la misma en las tres puertas del sistema:

  - `session.py check --packet`   → admisión de la tarea
  - `session.py verify --packet`  → antes de mergear
  - `board.py lint`               → validación del tablero

Si estuviera duplicada, un cambio en la matriz dejaría dos verdades. No depende de ningún otro
módulo de `agents/tools`.
"""

from __future__ import annotations

import json
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
PERMISSIONS = PROJECT_ROOT / "agents" / "permissions.json"

LEVELS = {"none": 0, "read": 1, "write": 2}


def load_permissions() -> dict:
    try:
        data = json.loads(PERMISSIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def permission_errors(packet: dict, role: str) -> list[str]:
    """Comprueba que el packet no pide más de lo que la matriz concede al rol.

    Las rutas no se pueden comparar entre productos (las categorías se materializan de forma
    abstracta), pero el nivel por categoría sí: es donde vive la decisión de permisos. Sin esta
    comprobación, el packet solo era coherente consigo mismo.
    """
    document = load_permissions()
    matrix = document.get("matrix") or {}
    known = document.get("categories") or {}

    row = matrix.get(role)
    if not isinstance(row, dict):
        return [f"el rol '{role}' no aparece en agents/permissions.json"]

    declared = packet.get("categories")
    if not isinstance(declared, dict) or not declared:
        return ["el packet no declara 'categories': sin categorías no se puede comprobar el permiso"]

    errors: list[str] = []
    for category, level in declared.items():
        if category not in known:
            errors.append(f"categoría desconocida '{category}': no está en agents/permissions.json")
            continue
        if level not in LEVELS:
            errors.append(f"nivel inválido '{level}' en '{category}' (none|read|write)")
            continue
        allowed = row.get(category, "none")
        if LEVELS[level] > LEVELS.get(allowed, 0):
            errors.append(
                f"'{role}' no tiene '{category}' a nivel {level}: la matriz concede '{allowed}'"
            )
    return errors
