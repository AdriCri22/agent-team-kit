"""Valida el tablero de planificación antes de publicarlo.

Uso:
  python3 agents/tools/board.py lint --board <backlog.json>

Códigos de salida: 0 = tablero coherente, 1 = violaciones, 2 = error de uso.

Convierte en comprobaciones lo que el orquestador promete en prosa: estados válidos, dueño real,
dependencias cerradas antes de entrar en curso, rama y PR donde toca, especificación aprobada,
firmas requeridas completas al cerrar y dos tareas de la misma ola sin solapar el write set de
sus packets.

Además valida cada packet de una tarea activa: que sea del mismo rol y de la misma especificación
que la tarea, y que sus categorías no excedan la matriz de permisos (misma comprobación que
`session.py check`, compartida en `authz.py`).

Las rutas del tablero (`spec`, `packet`) se resuelven contra el directorio del tablero y, si no
existen, contra la raíz del producto (el directorio que contiene `agents/`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

from authz import permission_errors
from paths import is_literal, path_pattern

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLES_REGISTRY = PROJECT_ROOT / "agents" / "registry.json"

STATUSES = {"todo", "in_progress", "blocked", "review", "done"}
APPROVED = {"approved", "building", "released"}
ACTIVE = {"in_progress", "review"}


def known_roles() -> set[str]:
    try:
        data = json.loads(ROLES_REGISTRY.read_text(encoding="utf-8"))
        return {str(entry["id"]) for entry in data.get("roles", []) if "id" in entry}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def resolve(raw: str, base: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(raw)
    if path.is_absolute():
        return path
    beside_board = (base / path).resolve()
    return beside_board if beside_board.exists() else (PROJECT_ROOT / path).resolve()


def segments(pattern: str) -> list[str]:
    return pattern.replace("\\", "/").split("/")


def affixes(segment: str) -> tuple[str, str]:
    wildcards = [index for index, char in enumerate(segment) if char in "*?"]
    if not wildcards:
        return segment, ""
    return segment[: wildcards[0]], segment[wildcards[-1] + 1 :]


def segment_overlap(left: str, right: str) -> bool:
    """¿Pueden describir el mismo segmento? Conservador cuando no se puede decidir."""
    if left == "**" or right == "**":
        return True
    if is_literal(left) and is_literal(right):
        return left == right
    if is_literal(left):
        return bool(path_pattern(right).match(left))
    if is_literal(right):
        return bool(path_pattern(left).match(right))

    left_prefix, left_suffix = affixes(left)
    right_prefix, right_suffix = affixes(right)
    if left_prefix and right_prefix and not (
        left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)
    ):
        return False
    if left_suffix and right_suffix and not (
        left_suffix.endswith(right_suffix) or right_suffix.endswith(left_suffix)
    ):
        return False
    return True


def star_tail(rest: list[str], other: list[str]) -> bool:
    """Un lado ya consumió `**`: sus restricciones tienen que caber en la cola del otro."""
    if not rest:
        return True
    if len(rest) > len(other):
        return False
    for one, two in zip(reversed(rest), reversed(other)):
        if not segment_overlap(one, two):
            return False
    return True


def globs_overlap(left: str, right: str) -> bool:
    """Intersección de dos patrones por segmentos, no exhaustiva pero sin falsos negativos claros."""
    if left == right:
        return True
    if is_literal(left) and is_literal(right):
        return False
    if is_literal(left):
        return bool(path_pattern(right).match(left))
    if is_literal(right):
        return bool(path_pattern(left).match(right))

    one, two = segments(left), segments(right)
    index = 0
    while index < len(one) and index < len(two):
        if one[index] == "**":
            return star_tail(one[index + 1 :], two[index:])
        if two[index] == "**":
            return star_tail(two[index + 1 :], one[index:])
        if not segment_overlap(one[index], two[index]):
            return False
        index += 1
    return len(one) == len(two)


def overlaps(left: list[str], right: list[str]) -> list[tuple[str, str]]:
    """Solapes entre dos write sets, también cuando los dos lados son patrones."""
    hits: list[tuple[str, str]] = []
    for one in left:
        for other in right:
            if globs_overlap(one, other):
                hits.append((one, other))
    return hits


def spec_problem(spec: pathlib.Path) -> str | None:
    if not spec.is_file():
        return f"la especificación '{spec.name}' no existe"
    try:
        data = yaml.safe_load(spec.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return f"la especificación no se puede leer ({exc})"
    if not isinstance(data, dict):
        return "la especificación no es un mapa"
    status = data.get("status")
    if status not in APPROVED:
        return f"la especificación no está aprobada (status {status!r})"
    approved_by = data.get("approved_by")
    if not isinstance(approved_by, list) or not approved_by:
        return "la especificación no tiene approved_by"
    return None


def read_task_packet(task: dict, base: pathlib.Path) -> tuple[dict | None, str | None]:
    raw = task.get("packet")
    if not isinstance(raw, str) or not raw:
        return None, "no declara packet"
    path = resolve(raw, base)
    if not path.is_file():
        return None, f"no encuentro el packet '{raw}'"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"el packet '{raw}' no se puede leer ({exc})"
    if not isinstance(data, dict):
        return None, f"el packet '{raw}' no es un objeto JSON"
    return data, None


def packet_writes(task: dict, base: pathlib.Path) -> tuple[list[str] | None, str | None]:
    data, problem = read_task_packet(task, base)
    if problem:
        return None, problem
    writes = data.get("write")
    if not isinstance(writes, list) or not writes:
        return None, f"el packet '{task.get('packet')}' no declara write"
    return [str(item) for item in writes], None


def cycle_errors(by_id: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        state[node] = 1
        for dep in by_id[node].get("deps") or []:
            if dep not in by_id:
                continue
            if state.get(dep) == 1:
                errors.append(f"dependencias con ciclo: {' → '.join(trail + [dep])}")
            elif state.get(dep, 0) == 0:
                visit(dep, trail + [dep])
        state[node] = 2

    for task_id in by_id:
        if state.get(task_id, 0) == 0:
            visit(task_id, [task_id])
    return errors


def overlap_errors(by_id: dict[str, dict], base: pathlib.Path) -> list[str]:
    errors: list[str] = []
    waves: dict[object, list[str]] = {}
    for task_id, task in by_id.items():
        if task.get("status") in ACTIVE:
            waves.setdefault(task.get("wave"), []).append(task_id)

    for wave, ids in sorted(waves.items(), key=lambda item: (str(type(item[0])), str(item[0]))):
        writes: dict[str, list[str]] = {}
        for task_id in sorted(ids):
            found, problem = packet_writes(by_id[task_id], base)
            if problem:
                # El packet que falta ya se reporta al validar la tarea; aquí no se duplica.
                continue
            writes[task_id] = found or []
        ordered = sorted(writes)
        for position, left in enumerate(ordered):
            for right in ordered[position + 1 :]:
                hits = overlaps(writes[left], writes[right])
                if hits:
                    errors.append(
                        f"ola {wave}: '{left}' y '{right}' escriben la misma superficie"
                        f" ({hits[0][0]})"
                    )
    return errors


def lint(board: dict, base: pathlib.Path) -> list[str]:
    tasks = board.get("tasks")
    if not isinstance(tasks, list):
        return ["el tablero no declara 'tasks' como lista"]

    roles = known_roles()
    errors: list[str] = []
    by_id: dict[str, dict] = {}

    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"tasks[{index}] no es un mapa")
            continue
        task_id = task.get("task")
        if not isinstance(task_id, str) or not task_id:
            errors.append(f"tasks[{index}] sin identificador")
            continue
        if task_id in by_id:
            errors.append(f"tarea duplicada '{task_id}'")
            continue
        by_id[task_id] = task

    for task_id, task in by_id.items():
        status = task.get("status")
        if status not in STATUSES:
            errors.append(f"{task_id}: estado '{status}' no válido (todo|in_progress|blocked|review|done)")

        owner = task.get("owner")
        if isinstance(owner, str) and roles and owner not in roles:
            errors.append(f"{task_id}: dueño '{owner}' no es un rol de agents/registry.json")
        if status != "todo" and not owner:
            errors.append(f"{task_id}: en '{status}' sin dueño")

        wave = task.get("wave")
        if not isinstance(wave, int) or isinstance(wave, bool):
            errors.append(f"{task_id}: 'wave' debe ser un entero")
            wave = 0

        deps = task.get("deps") or []
        if not isinstance(deps, list):
            errors.append(f"{task_id}: 'deps' debe ser lista")
            deps = []
        for dep in deps:
            if not isinstance(dep, str) or dep not in by_id:
                errors.append(f"{task_id}: dependencia '{dep}' no existe")
                continue
            if dep == task_id:
                errors.append(f"{task_id}: se depende a sí misma")
                continue
            dependency = by_id[dep]
            dep_wave = dependency.get("wave")
            if isinstance(dep_wave, int) and isinstance(wave, int) and dep_wave > wave:
                errors.append(f"{task_id}: depende de '{dep}', que va en una ola posterior")
            if status in {"in_progress", "review", "done"} and dependency.get("status") != "done":
                errors.append(f"{task_id}: está en '{status}' con la dependencia '{dep}' abierta")

        branch = task.get("branch")
        pr = task.get("pr")
        if status == "in_progress" and not branch:
            errors.append(f"{task_id}: en curso sin rama")
        if status in {"review", "done"} and not pr:
            errors.append(f"{task_id}: en '{status}' sin PR")
        if status == "done" and not branch:
            errors.append(f"{task_id}: cerrada sin rama")
        if status == "blocked" and not str(task.get("notes") or "").strip():
            errors.append(f"{task_id}: bloqueada sin motivo en notes")

        if status in {"in_progress", "review", "done"}:
            spec = task.get("spec")
            if not isinstance(spec, str) or not spec:
                errors.append(f"{task_id}: en '{status}' sin especificación")
            else:
                problem = spec_problem(resolve(spec, base))
                if problem:
                    errors.append(f"{task_id}: {problem}")

            packet, packet_problem = read_task_packet(task, base)
            if packet_problem:
                errors.append(f"{task_id}: {packet_problem}")
            else:
                packet_role = packet.get("role")
                if packet_role != owner:
                    errors.append(
                        f"{task_id}: el packet es de '{packet_role}', no del dueño '{owner}'"
                    )
                packet_spec = packet.get("spec")
                if isinstance(packet_spec, str) and isinstance(spec, str):
                    if resolve(packet_spec, base) != resolve(spec, base):
                        errors.append(f"{task_id}: el packet apunta a otra especificación")
                if isinstance(owner, str):
                    for item in permission_errors(packet, owner):
                        errors.append(f"{task_id}: {item}")

        required = task.get("signatures_required")
        signed = task.get("signatures")
        if not isinstance(required, list) or not isinstance(signed, dict):
            errors.append(f"{task_id}: declara signatures_required (lista) y signatures (mapa)")
        else:
            if set(signed) != set(required):
                errors.append(f"{task_id}: signatures debe tener un hueco por rol requerido")
            for role_id in required:
                if roles and role_id not in roles:
                    errors.append(f"{task_id}: firma requerida '{role_id}' no es un rol")
            if status == "done":
                pending = sorted(role_id for role_id in required if not signed.get(role_id))
                if pending:
                    errors.append(f"{task_id}: cerrada con firmas pendientes: {', '.join(pending)}")

    errors.extend(cycle_errors(by_id))
    errors.extend(overlap_errors(by_id, base))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida el tablero de planificación.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint")
    lint_parser.add_argument("--board", required=True)
    args = parser.parse_args()

    board_path = pathlib.Path(args.board)
    if not board_path.is_file():
        print(f"no encuentro el tablero '{args.board}'", file=sys.stderr)
        return 2

    try:
        board = json.loads(board_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"no se puede leer el tablero: {exc}", file=sys.stderr)
        return 2
    if not isinstance(board, dict):
        print("el tablero no es un objeto JSON", file=sys.stderr)
        return 2

    errors = lint(board, board_path.parent)
    if errors:
        print(f"{len(errors)} violación(es) en el tablero:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"tablero coherente · {len(board.get('tasks') or [])} tareas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
