"""Patrones de ruta de un write set.

Semántica única para todas las herramientas: `*` se queda en un nivel, `?` en un carácter y `**`
cruza lo que haga falta. `fnmatch` deja que `*` cruce `/`, y con eso `src/*.ts` autorizaría
`src/a/b.ts`: una ampliación silenciosa de la superficie.

La usan `session.py verify` y `board.py lint`; si viviera duplicada, un cambio en un sitio dejaría
dos definiciones de "qué está dentro del write set".
"""

from __future__ import annotations

import re


def is_literal(pattern: str) -> bool:
    return not any(char in pattern for char in "*?")


def path_pattern(pattern: str) -> re.Pattern[str]:
    pattern = pattern.replace("\\", "/")
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                parts.append(".*")
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    index += 1
                continue
            parts.append("[^/]*")
            index += 1
            continue
        if char == "?":
            parts.append("[^/]")
            index += 1
            continue
        parts.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(parts) + "$")
