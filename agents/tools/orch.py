"""CLI de orquestación: tablero, packets, tickets, gates y worktrees en un solo sitio.

Sustituye los scripts de un solo uso del orquestador. Todas las mutaciones conservan el
formato del tablero (CRLF si lo tenía, `ensure_ascii=False`, indentación de 2 y salto final)
y son atómicas: se escribe un temporal y se reemplaza.

Uso:
  python3 agents/tools/orch.py --root <producto> board list
  python3 agents/tools/orch.py --root <producto> board add CAP-08 --title ... --spec ... \
      --owner backend-engineer --wave 34 --branch feat/cap-08 --signatures tech-lead-backend,qa-lead
  python3 agents/tools/orch.py --root <producto> packet new CAP-08
  python3 agents/tools/orch.py --root <producto> tickets list --open
  python3 agents/tools/orch.py --root <producto> gates CAP-08
  python3 agents/tools/orch.py --root <producto> worktree add CAP-08 --branch feat/cap-08 --node-modules

Códigos: 0 = bien, 1 = comprobación fallida, 2 = error de uso.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

import yaml

KIT_ROOT = pathlib.Path(__file__).resolve().parents[2]
STATUSES = ["todo", "in_progress", "blocked", "review", "done"]
SEVERITIES = ["S1", "S2", "S3", "S4"]
DEFAULT_SIGNATURES = ["tech-lead-backend", "qa-lead", "product-owner"]


# --------------------------------------------------------------------------- rutas


def product_root(raw: str | None) -> pathlib.Path:
    root = pathlib.Path(raw).resolve() if raw else pathlib.Path.cwd().resolve()
    if not (root / "board.json").is_file() and not (root / "docs").is_dir():
        raise SystemExit(f"'{root}' no parece la raíz del producto (sin board.json ni docs/)")
    return root


def kit_prefix(root: pathlib.Path) -> str:
    """Prefijo con el que los packets referencian el sistema de agentes."""
    return "agent-team-kit" if (root / "agent-team-kit" / "agents").is_dir() else ""


def tools_dir(root: pathlib.Path) -> pathlib.Path:
    for candidate in (root / "agents" / "tools", root / "agent-team-kit" / "agents" / "tools"):
        if (candidate / "session.py").is_file():
            return candidate
    return KIT_ROOT / "agents" / "tools"


def board_path(root: pathlib.Path, raw: str | None) -> pathlib.Path:
    path = pathlib.Path(raw) if raw else pathlib.Path("board.json")
    return path if path.is_absolute() else root / path


# --------------------------------------------------------------------------- tablero


def read_board(path: pathlib.Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"no encuentro el tablero '{path}'")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"el tablero no es JSON válido: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("el tablero no es un objeto JSON")
    return data


def board_newline(path: pathlib.Path) -> str:
    try:
        return "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    except OSError:
        return "\n"


def write_board(path: pathlib.Path, board: dict) -> None:
    text = json.dumps(board, ensure_ascii=False, indent=2) + "\n"
    if board_newline(path) == "\r\n":
        text = text.replace("\n", "\r\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".board-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temp, path)
    except BaseException:
        pathlib.Path(temp).unlink(missing_ok=True)
        raise


def append_progress(board: dict, text: str) -> None:
    board["progress"] = ((board.get("progress") or "").rstrip() + " " + text).strip()


def find_task(board: dict, task_id: str) -> dict:
    for task in board.get("tasks") or []:
        if task.get("task") == task_id:
            return task
    raise SystemExit(f"no hay tarea '{task_id}' en el tablero")


def split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def known_roles() -> set[str]:
    registry = KIT_ROOT / "agents" / "registry.json"
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(entry["id"]) for entry in data.get("roles", []) if "id" in entry}


def require_roles(roles: list[str]) -> None:
    valid = known_roles()
    unknown = [role for role in roles if valid and role not in valid]
    if unknown:
        raise SystemExit(f"roles desconocidos: {', '.join(unknown)}")


# --------------------------------------------------------------------------- fichas


def task_file(root: pathlib.Path, task_id: str) -> pathlib.Path:
    return root / "docs" / "planning" / "tasks" / f"{task_id}.yaml"


def read_task_file(root: pathlib.Path, task_id: str) -> dict:
    path = task_file(root, task_id)
    if not path.is_file():
        raise SystemExit(f"no encuentro la ficha '{path}'")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"la ficha '{path}' no es un objeto YAML")
    return data


def packet_from_task(root: pathlib.Path, task_id: str, role_override: str | None) -> dict:
    ficha = read_task_file(root, task_id)
    role = role_override or ficha.get("owner")
    if not role:
        raise SystemExit("la ficha no declara owner; pasa --role")
    surface = ficha.get("surface") or {}
    prefix = kit_prefix(root)
    kit = f"{prefix}/agents" if prefix else "agents"
    must = [
        f"{kit}/cards/{role}.yaml",
        f"memory/{role}.yaml",
        f"{kit}/policy/delivery.yaml",
        ficha.get("spec"),
        ficha.get("common_rules") or "docs/planning/tasks/_common.yaml",
        f"docs/planning/tasks/{task_id}.yaml",
    ]
    must = [item for item in must if item]
    return {
        "_doc": (
            "Packet de tarea. Lo emite orchestrator y lo consume un solo worker. Las rutas se "
            "derivan de agent-team-kit/agents/permissions.json. `categories` declara el nivel "
            "necesario; session.py check comprueba que no excede la fila del rol."
        ),
        "task": task_id,
        "title": ficha.get("title"),
        "role": role,
        "independent": False,
        "spec": ficha.get("spec"),
        "acceptance_refs": ficha.get("acceptance_refs") or [],
        "session": {"mode": "new", "id": None},
        "read": {
            "must": must,
            "allowed": surface.get("allowed") or [],
            "forbidden": surface.get("forbidden") or [],
        },
        "write": surface.get("write") or [],
        "categories": ficha.get("categories") or {},
        "gates": ficha.get("gates") or [],
        "depends_on": ficha.get("depends_on") or [],
        "out_of_scope": ficha.get("out_of_scope") or [],
        "handoff": "agent-team-kit/agents/policy/delivery.yaml#handoff",
    }


# --------------------------------------------------------------------------- tickets


def tickets_dir(root: pathlib.Path) -> pathlib.Path:
    return root / "tickets"


def read_ticket(path: pathlib.Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        print(f"aviso: YAML inválido en {path}: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def find_ticket(root: pathlib.Path, ticket_id: str) -> pathlib.Path:
    candidates = sorted(tickets_dir(root).glob(f"{ticket_id}-*.yaml")) + sorted(
        tickets_dir(root).glob(f"{ticket_id}.yaml")
    )
    if not candidates:
        raise SystemExit(f"no encuentro el ticket '{ticket_id}' en {tickets_dir(root)}")
    return candidates[0]


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:60] or "ticket"


def write_yaml(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------- comandos


def cmd_board_list(args) -> int:
    root = product_root(args.root)
    board = read_board(board_path(root, args.board))
    tasks = board.get("tasks") or []
    if args.status:
        wanted = set(split_csv(args.status))
        tasks = [task for task in tasks if task.get("status") in wanted]
    if args.owner:
        tasks = [task for task in tasks if task.get("owner") == args.owner]
    if args.wave:
        tasks = [task for task in tasks if task.get("wave") == args.wave]
    if args.json:
        print(json.dumps(tasks, ensure_ascii=False, indent=1))
        return 0
    if not tasks:
        print("sin tareas que cumplan el filtro")
        return 0
    for task in tasks:
        print(
            f"{task.get('task'):<12} {str(task.get('status')):<12} "
            f"{str(task.get('owner')):<22} ola {str(task.get('wave')):<3} "
            f"deps {','.join(task.get('deps') or []) or '-'}"
        )
    print(f"{len(tasks)} tarea(s)")
    return 0


def cmd_board_show(args) -> int:
    root = product_root(args.root)
    board = read_board(board_path(root, args.board))
    print(json.dumps(find_task(board, args.task), ensure_ascii=False, indent=2))
    return 0


def cmd_board_add(args) -> int:
    root = product_root(args.root)
    path = board_path(root, args.board)
    board = read_board(path)
    if any(task.get("task") == args.task for task in board.get("tasks") or []):
        raise SystemExit(f"la tarea '{args.task}' ya existe")
    if args.status not in STATUSES:
        raise SystemExit(f"estado no válido: {args.status}")
    signatures = split_csv(args.signatures) or list(DEFAULT_SIGNATURES)
    require_roles(signatures + [args.owner])
    task = {
        "task": args.task,
        "title": args.title,
        "spec": args.spec,
        "owner": args.owner,
        "status": args.status,
        "wave": args.wave,
        "deps": split_csv(args.deps),
        "branch": args.branch,
        "pr": args.pr,
        "packet": args.packet or f"packets/{args.task}.json",
        "signatures_required": signatures,
        "signatures": {role: None for role in signatures},
        "progress": "planned",
        "notes": args.notes or "",
    }
    if task["status"] == "in_progress" and not task["branch"]:
        raise SystemExit("una tarea en curso necesita --branch")
    board.setdefault("tasks", []).append(task)
    append_progress(board, args.progress or f"{args.task} admitida en la ola {args.wave}.")
    board["updated_at"] = args.date or board.get("updated_at")
    write_board(path, board)
    print(f"{args.task} añadida · {len(board['tasks'])} tareas")
    return 0


def cmd_board_set(args) -> int:
    root = product_root(args.root)
    path = board_path(root, args.board)
    board = read_board(path)
    task = find_task(board, args.task)
    if args.status:
        if args.status not in STATUSES:
            raise SystemExit(f"estado no válido: {args.status}")
        task["status"] = args.status
    for field in ("owner", "branch", "pr", "packet", "title", "spec"):
        value = getattr(args, field, None)
        if value is not None:
            task[field] = value
    if args.wave is not None:
        task["wave"] = args.wave
    if args.deps is not None:
        task["deps"] = split_csv(args.deps)
    if args.notes is not None:
        task["notes"] = args.notes
    if args.append_notes:
        task["notes"] = ((task.get("notes") or "").rstrip() + " " + args.append_notes).strip()
    if args.status == "in_progress" and not task.get("branch"):
        raise SystemExit("una tarea en curso necesita rama")
    if args.date:
        board["updated_at"] = args.date
    write_board(path, board)
    print(f"{args.task} actualizada · estado {task.get('status')}")
    return 0


def cmd_board_sign(args) -> int:
    root = product_root(args.root)
    path = board_path(root, args.board)
    board = read_board(path)
    task = find_task(board, args.task)
    if args.role == task.get("owner"):
        raise SystemExit("el autor del trabajo no puede firmar su propia tarea")
    if args.role not in (task.get("signatures") or {}):
        raise SystemExit(f"'{args.role}' no es una firma requerida de {args.task}")
    text = sys.stdin.read().strip() if args.text == "-" else args.text
    if not text:
        raise SystemExit("la firma necesita texto (--text o --text -)")
    task["signatures"][args.role] = text
    pending = [role for role, value in task["signatures"].items() if not value]
    write_board(path, board)
    print(f"{args.task} · firma de {args.role} registrada · pendientes: {', '.join(pending) or 'ninguna'}")
    return 0


def cmd_board_done(args) -> int:
    root = product_root(args.root)
    path = board_path(root, args.board)
    board = read_board(path)
    task = find_task(board, args.task)
    pending = [role for role, value in (task.get("signatures") or {}).items() if not value]
    if pending:
        raise SystemExit(f"{args.task} no puede cerrarse: firmas pendientes {', '.join(pending)}")
    if not args.pr and not task.get("pr"):
        raise SystemExit("una tarea cerrada necesita --pr")
    task["status"] = "done"
    if args.pr:
        task["pr"] = args.pr
    if args.note:
        task["notes"] = ((task.get("notes") or "").rstrip() + " " + args.note).strip()
    append_progress(board, args.progress or f"{args.task} cerrada ({args.pr or task.get('pr')}).")
    if args.date:
        board["updated_at"] = args.date
    write_board(path, board)
    print(f"{args.task} → done")
    return 0


def cmd_board_progress(args) -> int:
    root = product_root(args.root)
    path = board_path(root, args.board)
    board = read_board(path)
    text = sys.stdin.read().strip() if args.text == "-" else args.text
    if not text:
        raise SystemExit("falta el texto del progreso")
    append_progress(board, text)
    if args.date:
        board["updated_at"] = args.date
    write_board(path, board)
    print("progreso añadido")
    return 0


def cmd_board_remove(args) -> int:
    root = product_root(args.root)
    path = board_path(root, args.board)
    board = read_board(path)
    closed = set(args.tasks)
    present = {task.get("task") for task in board.get("tasks") or []}
    missing = closed - present
    if missing:
        raise SystemExit(f"no están en el tablero: {', '.join(sorted(missing))}")
    board["tasks"] = [task for task in board.get("tasks") or [] if task.get("task") not in closed]
    for task in board["tasks"]:
        task["deps"] = [dep for dep in task.get("deps") or [] if dep not in closed]
    board["remaining_work"] = [
        item for item in board.get("remaining_work") or [] if item.get("task") not in closed
    ]
    append_progress(
        board,
        args.progress
        or f"{', '.join(sorted(closed))} retirada(s) del tablero: quedan {len(board['tasks'])} tareas.",
    )
    if args.date:
        board["updated_at"] = args.date
    write_board(path, board)
    print(f"retiradas {len(closed)} · quedan {len(board['tasks'])} tareas")
    return 0


def cmd_packet_new(args) -> int:
    root = product_root(args.root)
    packet = packet_from_task(root, args.task, args.role)
    if args.must:
        packet["read"]["must"].extend(split_csv(args.must))
    out = pathlib.Path(args.out) if args.out else root / "packets" / f"{args.task}.json"
    if out.exists() and not args.force:
        raise SystemExit(f"'{out}' ya existe (usa --force)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"packet escrito: {out} · {len(packet['write'])} patrones de escritura")
    return 0


def cmd_tickets_list(args) -> int:
    root = product_root(args.root)
    wanted = set(split_csv(args.severity)) if args.severity else set()
    rows = []
    for path in sorted(tickets_dir(root).glob("*.yaml")):
        data = read_ticket(path)
        if not data:
            continue
        if args.open and data.get("status") != "open":
            continue
        if wanted and data.get("severity") not in wanted:
            continue
        rows.append(data)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for data in rows:
        print(
            f"{str(data.get('ticket')):<8} {str(data.get('severity')):<3} "
            f"{str(data.get('status')):<12} {str(data.get('owner')):<18} "
            f"{str(data.get('title'))[:70]}"
        )
    print(f"{len(rows)} ticket(s)")
    return 0


def cmd_ticket_new(args) -> int:
    root = product_root(args.root)
    if args.severity not in SEVERITIES:
        raise SystemExit(f"severidad no válida: {args.severity}")
    slug = args.slug or slugify(args.title)
    path = tickets_dir(root) / f"{args.ticket}-{slug}.yaml"
    if path.exists() and not args.force:
        raise SystemExit(f"'{path}' ya existe (usa --force)")
    data = {
        "ticket": args.ticket,
        "title": args.title,
        "severity": args.severity,
        "reported_by": args.reported_by,
        "owner": args.owner,
        "environment": args.environment or "",
        "version": args.version or "",
        "status": "open",
        "spec": args.spec,
        "acceptance_refs": split_csv(args.refs),
        "what_happens": (args.what or "") + "\n",
        "steps": split_csv(args.steps),
        "expected": (args.expected or "") + "\n",
        "actual": (args.actual or "") + "\n",
        "evidence": split_csv(args.evidence),
        "impact": (args.impact or "") + "\n",
        "root_cause": "",
        "closure_criteria": [
            "reproducido por quien reporta",
            "corregido en rama propia con PR",
            "verificado por qa-lead",
            "prueba de regresión en la suite",
        ],
        "notes": (args.notes or "") + "\n",
    }
    write_yaml(path, data)
    print(f"ticket escrito: {path}")
    return 0


def cmd_ticket_close(args) -> int:
    root = product_root(args.root)
    path = find_ticket(root, args.ticket)
    data = read_ticket(path)
    if data.get("status") == "closed" and not args.force:
        print(f"{data.get('ticket')} ya estaba cerrado")
        return 0
    data["status"] = "closed"
    closure = args.note or ""
    if args.pr:
        closure = (closure + f"\nCerrado con {args.pr}.").strip()
    if closure:
        data["closure"] = closure + "\n"
    write_yaml(path, data)
    print(f"{data.get('ticket')} → closed · {path.name}")
    return 0


def cmd_gates(args) -> int:
    root = product_root(args.root)
    packet_path = pathlib.Path(args.packet) if args.packet else root / "packets" / f"{args.task}.json"
    if not packet_path.is_file():
        raise SystemExit(f"no encuentro el packet '{packet_path}'")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    gates = packet.get("gates") or []
    if args.only:
        wanted = split_csv(args.only)
        gates = [gate for gate in gates if gate.get("name") in wanted]
        if not gates:
            raise SystemExit(f"ningún gate coincide con {args.only}")
    timeout = args.timeout
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "CI": os.environ.get("CI", "")}
    failures = 0
    for gate in gates:
        name = gate.get("name") or "gate"
        command = gate.get("command") or ""
        if not command:
            print(f"· {name:<28} SIN COMANDO")
            failures += 1
            continue
        if args.dry_run:
            print(f"· {name:<28} ({command})")
            continue
        started = time.monotonic()
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
        elapsed = time.monotonic() - started
        state = "OK" if result.returncode == 0 else "FALLA"
        print(f"· {name:<28} {state:<5} {elapsed:6.1f}s")
        if result.returncode != 0:
            failures += 1
            output = ((result.stdout or "") + (result.stderr or "")).strip().splitlines()
            for line in output[-args.tail :]:
                print(f"    {line}")
    if args.dry_run:
        return 0
    print(f"{len(gates) - failures}/{len(gates)} gates en verde")
    return 1 if failures else 0


def cmd_pr_body(args) -> int:
    root = product_root(args.root)
    ficha = read_task_file(root, args.task)
    packet_path = root / "packets" / f"{args.task}.json"
    packet = {}
    if packet_path.is_file():
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    lines = [f"## {ficha.get('task')} · {ficha.get('title')}", ""]
    objective = (ficha.get("objective") or "").strip()
    if objective:
        lines += [objective, ""]
    refs = ficha.get("acceptance_refs") or []
    if refs:
        lines += ["### Criterios de aceptación", ""]
        lines += [f"- `{ref}`" for ref in refs]
        lines.append("")
    write = (packet.get("write") or (ficha.get("surface") or {}).get("write") or [])
    if write:
        lines += ["### Write set", ""]
        lines += [f"- `{item}`" for item in write]
        lines.append("")
    gates = packet.get("gates") or ficha.get("gates") or []
    if gates:
        lines += ["### Gates", ""]
        for gate in gates:
            lines.append(f"- `{gate.get('name')}`: `{gate.get('command')}`")
        lines.append("")
    lines += [
        "### Evidencia",
        "",
        "- [ ] Gates ejecutados y su resultado pegados aquí",
        "- [ ] Criterios de aceptación citados con evidencia",
        "- [ ] Firmas de `signatures_required` (ninguna del autor)",
        "",
    ]
    text = "\n".join(lines)
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(f"cuerpo de PR escrito: {args.out}")
    else:
        print(text)
    return 0


def run_git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def worktree_path(root: pathlib.Path, task_id: str, raw: str | None) -> pathlib.Path:
    return pathlib.Path(raw) if raw else root / ".orchestrator" / "worktrees" / task_id


def cmd_worktree_add(args) -> int:
    root = product_root(args.root)
    target = worktree_path(root, args.task, args.path)
    if target.exists():
        raise SystemExit(f"'{target}' ya existe")
    branches = run_git(root, "branch", "--list", args.branch).stdout.strip()
    remote = run_git(root, "branch", "-r", "--list", f"origin/{args.branch}").stdout.strip()
    if branches or remote:
        command = ["worktree", "add", str(target), args.branch]
    else:
        command = ["worktree", "add", str(target), "-b", args.branch, args.base]
    result = run_git(root, *command)
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        return 1
    print(f"worktree: {target} · rama {args.branch}")
    if args.node_modules:
        sources = [root / "node_modules", root / "web" / "node_modules", root / "dashboard" / "node_modules"]
        for source in sources:
            if not source.is_dir():
                continue
            destination = target / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(destination), str(source)],
                    capture_output=True,
                    text=True,
                )
            else:
                created = subprocess.run(
                    ["ln", "-s", str(source), str(destination)], capture_output=True, text=True
                )
            state = "ok" if created.returncode == 0 else "falló"
            print(f"  node_modules {source.relative_to(root)}: {state}")
    print("  recuerda: los puertos 8787/8788 no se comparten; ejecuta una suite e2e a la vez.")
    return 0


def cmd_worktree_remove(args) -> int:
    root = product_root(args.root)
    target = worktree_path(root, args.task, args.path)
    result = run_git(root, "worktree", "remove", str(target), "--force")
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        return 1
    run_git(root, "worktree", "prune")
    print(f"worktree retirado: {target}")
    return 0


def cmd_worktree_list(args) -> int:
    root = product_root(args.root)
    print(run_git(root, "worktree", "list").stdout.rstrip())
    return 0


def cmd_close_task(args) -> int:
    root = product_root(args.root)
    path = board_path(root, args.board)
    board = read_board(path)
    task = find_task(board, args.task)
    owner = task.get("owner")
    pending = [role for role, value in (task.get("signatures") or {}).items() if not value]
    plan = [
        f"verify {args.task} en {args.branch}",
        f"firmas pendientes: {', '.join(pending) or 'ninguna'}",
        f"board done {args.task}",
        f"session close --role {owner} --task {args.task}",
        f"board remove {args.task}",
    ]
    if pending:
        print("no se puede cerrar: firmas pendientes " + ", ".join(pending), file=sys.stderr)
        return 1
    if args.dry_run:
        for step in plan:
            print(f"· {step}")
        return 0

    tools = tools_dir(root)
    verify = [
        sys.executable,
        str(tools / "session.py"),
        "verify",
        "--packet",
        str(pathlib.Path(args.packet) if args.packet else root / "packets" / f"{args.task}.json"),
        "--branch",
        args.branch,
    ]
    if args.base:
        verify += ["--base", args.base]
    result = subprocess.run(verify, cwd=str(root), text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print("verify no pasó; no se cierra", file=sys.stderr)
        return 1

    task["status"] = "done"
    if args.pr:
        task["pr"] = args.pr
    append_progress(board, f"{args.task} cerrada ({task.get('pr') or 'sin PR'}).")
    write_board(path, board)

    memory = args.memory or f"memory/{owner}.yaml"
    close = [
        sys.executable,
        str(tools / "session.py"),
        "close",
        "--role",
        owner,
        "--task",
        args.task,
        "--memory",
        memory,
        "--board",
        str(path),
    ]
    if not args.skip_session:
        result = subprocess.run(close, cwd=str(root), text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            print("session close no pasó; la tarea queda done en el tablero", file=sys.stderr)
            return 1

    closed = {args.task}
    board = read_board(path)
    board["tasks"] = [item for item in board.get("tasks") or [] if item.get("task") not in closed]
    for item in board["tasks"]:
        item["deps"] = [dep for dep in item.get("deps") or [] if dep not in closed]
    board["remaining_work"] = [
        entry for entry in board.get("remaining_work") or [] if entry.get("task") not in closed
    ]
    append_progress(board, f"quedan {len(board['tasks'])} tareas.")
    write_board(path, board)
    print(f"{args.task} cerrada y retirada · quedan {len(board['tasks'])} tareas")
    if args.body_out:
        args.task = args.task
        cmd_pr_body(argparse.Namespace(root=str(root), task=args.task, out=args.body_out))
    return 0


# --------------------------------------------------------------------------- CLI


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root", default=argparse.SUPPRESS, help="raíz del producto (por defecto, el cwd)"
    )
    parser.add_argument(
        "--board", default=argparse.SUPPRESS, help="ruta del tablero (por defecto board.json)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orquestación: tablero, packets, tickets y gates.")
    parser.add_argument("--root", default=None, help="raíz del producto (por defecto, el cwd)")
    parser.add_argument("--board", default="board.json", help="ruta del tablero")
    subparsers = parser.add_subparsers(dest="command", required=True)

    board = subparsers.add_parser("board", help="tablero")
    board_sub = board.add_subparsers(dest="board_command", required=True)

    listing = board_sub.add_parser("list")
    add_common(listing)
    listing.add_argument("--status")
    listing.add_argument("--owner")
    listing.add_argument("--wave", type=int)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=cmd_board_list)

    show = board_sub.add_parser("show")
    add_common(show)
    show.add_argument("task")
    show.set_defaults(handler=cmd_board_show)

    add = board_sub.add_parser("add")
    add_common(add)
    add.add_argument("task")
    add.add_argument("--title", required=True)
    add.add_argument("--spec", required=True)
    add.add_argument("--owner", required=True)
    add.add_argument("--wave", type=int, required=True)
    add.add_argument("--status", default="todo", choices=STATUSES)
    add.add_argument("--branch", default=None)
    add.add_argument("--pr", default=None)
    add.add_argument("--packet", default=None)
    add.add_argument("--deps", default=None)
    add.add_argument("--signatures", default=None)
    add.add_argument("--notes", default="")
    add.add_argument("--progress", default=None)
    add.add_argument("--date", default=None)
    add.set_defaults(handler=cmd_board_add)

    update = board_sub.add_parser("set")
    add_common(update)
    update.add_argument("task")
    update.add_argument("--status", default=None, choices=STATUSES)
    update.add_argument("--owner", default=None)
    update.add_argument("--wave", type=int, default=None)
    update.add_argument("--branch", default=None)
    update.add_argument("--pr", default=None)
    update.add_argument("--packet", default=None)
    update.add_argument("--title", default=None)
    update.add_argument("--spec", default=None)
    update.add_argument("--deps", default=None)
    update.add_argument("--notes", default=None)
    update.add_argument("--append-notes", default=None)
    update.add_argument("--date", default=None)
    update.set_defaults(handler=cmd_board_set)

    sign = board_sub.add_parser("sign")
    add_common(sign)
    sign.add_argument("task")
    sign.add_argument("--role", required=True)
    sign.add_argument("--text", required=True, help="texto de la firma; '-' para leer de stdin")
    sign.set_defaults(handler=cmd_board_sign)

    done = board_sub.add_parser("done")
    add_common(done)
    done.add_argument("task")
    done.add_argument("--pr", default=None)
    done.add_argument("--note", default=None)
    done.add_argument("--progress", default=None)
    done.add_argument("--date", default=None)
    done.set_defaults(handler=cmd_board_done)

    remove = board_sub.add_parser("remove")
    add_common(remove)
    remove.add_argument("tasks", nargs="+")
    remove.add_argument("--progress", default=None)
    remove.add_argument("--date", default=None)
    remove.set_defaults(handler=cmd_board_remove)

    progress = board_sub.add_parser("progress")
    add_common(progress)
    progress.add_argument("--text", required=True, help="texto; '-' para leer de stdin")
    progress.add_argument("--date", default=None)
    progress.set_defaults(handler=cmd_board_progress)

    packet = subparsers.add_parser("packet", help="packets")
    packet_sub = packet.add_subparsers(dest="packet_command", required=True)
    packet_new = packet_sub.add_parser("new")
    add_common(packet_new)
    packet_new.add_argument("task")
    packet_new.add_argument("--role", default=None)
    packet_new.add_argument("--must", default=None, help="rutas extra para read.must, separadas por comas")
    packet_new.add_argument("--out", default=None)
    packet_new.add_argument("--force", action="store_true")
    packet_new.set_defaults(handler=cmd_packet_new)

    tickets = subparsers.add_parser("tickets", help="tickets")
    tickets_sub = tickets.add_subparsers(dest="tickets_command", required=True)
    tickets_list = tickets_sub.add_parser("list")
    add_common(tickets_list)
    tickets_list.add_argument("--open", action="store_true")
    tickets_list.add_argument("--severity", default=None)
    tickets_list.add_argument("--json", action="store_true")
    tickets_list.set_defaults(handler=cmd_tickets_list)

    ticket = subparsers.add_parser("ticket", help="ticket individual")
    ticket_sub = ticket.add_subparsers(dest="ticket_command", required=True)
    ticket_new = ticket_sub.add_parser("new")
    add_common(ticket_new)
    ticket_new.add_argument("ticket")
    ticket_new.add_argument("--title", required=True)
    ticket_new.add_argument("--severity", required=True)
    ticket_new.add_argument("--spec", required=True)
    ticket_new.add_argument("--reported-by", default="orchestrator")
    ticket_new.add_argument("--owner", default="qa-lead")
    ticket_new.add_argument("--environment", default="")
    ticket_new.add_argument("--version", default="")
    ticket_new.add_argument("--refs", default=None)
    ticket_new.add_argument("--what", default="")
    ticket_new.add_argument("--steps", default=None)
    ticket_new.add_argument("--expected", default="")
    ticket_new.add_argument("--actual", default="")
    ticket_new.add_argument("--evidence", default=None)
    ticket_new.add_argument("--impact", default="")
    ticket_new.add_argument("--notes", default="")
    ticket_new.add_argument("--slug", default=None)
    ticket_new.add_argument("--force", action="store_true")
    ticket_new.set_defaults(handler=cmd_ticket_new)

    ticket_close = ticket_sub.add_parser("close")
    add_common(ticket_close)
    ticket_close.add_argument("ticket")
    ticket_close.add_argument("--note", default=None)
    ticket_close.add_argument("--pr", default=None)
    ticket_close.add_argument("--force", action="store_true")
    ticket_close.set_defaults(handler=cmd_ticket_close)

    gates = subparsers.add_parser("gates", help="ejecuta los gates del packet")
    gates.add_argument("task")
    gates.add_argument("--root", default=argparse.SUPPRESS)
    gates.add_argument("--packet", default=None)
    gates.add_argument("--only", default=None)
    gates.add_argument("--timeout", type=int, default=900)
    gates.add_argument("--tail", type=int, default=12)
    gates.add_argument("--dry-run", action="store_true")
    gates.set_defaults(handler=cmd_gates)

    body = subparsers.add_parser("pr-body", help="borrador de cuerpo de PR")
    body.add_argument("task")
    body.add_argument("--root", default=argparse.SUPPRESS)
    body.add_argument("--out", default=None)
    body.set_defaults(handler=cmd_pr_body)

    worktree = subparsers.add_parser("worktree", help="árboles de trabajo")
    worktree_sub = worktree.add_subparsers(dest="worktree_command", required=True)
    worktree_add = worktree_sub.add_parser("add")
    worktree_add.add_argument("task")
    worktree_add.add_argument("--root", default=argparse.SUPPRESS)
    worktree_add.add_argument("--branch", required=True)
    worktree_add.add_argument("--base", default="main")
    worktree_add.add_argument("--path", default=None)
    worktree_add.add_argument(
        "--node-modules", dest="node_modules", action="store_true", default=True,
        help="enlaza node_modules del árbol principal (por defecto)",
    )
    worktree_add.add_argument("--no-node-modules", dest="node_modules", action="store_false")
    worktree_add.set_defaults(handler=cmd_worktree_add)

    worktree_remove = worktree_sub.add_parser("remove")
    worktree_remove.add_argument("task")
    worktree_remove.add_argument("--root", default=argparse.SUPPRESS)
    worktree_remove.add_argument("--path", default=None)
    worktree_remove.set_defaults(handler=cmd_worktree_remove)

    worktree_list = worktree_sub.add_parser("list")
    worktree_list.add_argument("--root", default=argparse.SUPPRESS)
    worktree_list.set_defaults(handler=cmd_worktree_list)

    close_task = subparsers.add_parser("close-task", help="verify, firmas, done, session close y retirada")
    close_task.add_argument("task")
    close_task.add_argument("--root", default=argparse.SUPPRESS)
    close_task.add_argument("--board", default=argparse.SUPPRESS)
    close_task.add_argument("--branch", required=True)
    close_task.add_argument("--base", default=None)
    close_task.add_argument("--packet", default=None)
    close_task.add_argument("--pr", default=None)
    close_task.add_argument("--memory", default=None)
    close_task.add_argument("--skip-session", action="store_true")
    close_task.add_argument("--body-out", default=None)
    close_task.add_argument("--dry-run", action="store_true")
    close_task.set_defaults(handler=cmd_close_task)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except SystemExit:
        raise
    except subprocess.TimeoutExpired:
        print("gate agotado por tiempo", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
