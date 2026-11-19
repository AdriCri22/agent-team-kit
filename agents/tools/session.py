"""Decide si una sesión de agente se reutiliza o se rota, de forma determinista y sin supuestos
del proveedor.

Uso:
  python3 agents/tools/session.py open  --role <rol> --model <modelo> --domain <dominio> --spec <ruta> [--window N]
  python3 agents/tools/session.py decide --role <rol> --spec <ruta> --model <modelo> --domain <dominio>
                                       [--packet <packet.json> | --packet-bytes N] [--independent]
                                       [--continuation <tarea> | --follow-up] [--expected-turns N]
  python3 agents/tools/session.py close --role <rol> --task <id> [--turns N] [--memory <ruta>] [--board <ruta>]
  python3 agents/tools/session.py check --packet <packet.json>
  python3 agents/tools/session.py state [--role <rol>]

Dos reglas mandan, en este orden:

1. Legalidad: ¿siguen vigentes los documentos, el modelo y el dominio? (huella SHA-256).
2. Economía: ¿sale más barato reutilizar o rotar para la tarea que viene?

Reutilizar es el resultado por defecto cuando ambas pasan: el historial ya está pagado y rotar
exige handoff. Lo que impide la deriva no es rotar por costumbre, sino las comprobaciones que
no se pueden saltar: modelo y dominio obligatorios, huella de los documentos, revisión
independiente y los topes de tareas, turnos y contexto derivados de la ventana. La intención
declarada (`--continuation` o `--follow-up`) no autoriza nada: solo deja escrito a qué hilo
pertenece la tarea.

En `close`, `--turns N` declara los turnos de **esa** tarea; el registro guarda el acumulado de
la sesión. Si el journal tiene turnos para la sesión, mandan los del journal.

No hay umbrales fijos. La ventana de contexto se declara (`open --window N`, o el perfil) y de
ella salen los bytes que la sesión puede cargar y los turnos y tareas que caben. Los techos de
coherencia del perfil acotan lo que la ventana permitiría, porque una sesión larga arrastra
supuestos que ya nadie revisó aunque le sobre espacio.

Lo único que depende del entorno de ejecución —caché de prefijo y su coste— se lee de
`agents/policy/providers.yaml`. Si el perfil no existe o no declara esa capacidad, se aplica el
perfil conservador y la decisión sigue siendo válida: solo es más exigente para reutilizar.

Requiere PyYAML, igual que `validate.py`.

El registro se protege con un cerrojo entre procesos: dos workers pueden cerrar tareas a la vez
sin pisarse. Todo el estado y los documentos internos se resuelven contra la raíz del producto
(el directorio que contiene `agents/`), no contra el directorio desde el que se ejecute el CLI.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import yaml

from authz import permission_errors
from paths import path_pattern

# Ancla del sistema: el producto puede tener `agents/` en su raíz o llevar el sistema dentro de un
# repositorio mayor. En ambos casos la raíz del producto es el directorio que contiene `agents/`,
# y desde ahí se resuelven documentos internos y estado. Nunca desde el CWD.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PROJECT_ROOT / "sessions.json"
PROVIDERS = PROJECT_ROOT / "agents" / "policy" / "providers.yaml"
ROLES_REGISTRY = PROJECT_ROOT / "agents" / "registry.json"
SESSIONS_POLICY = PROJECT_ROOT / "agents" / "policy" / "sessions.yaml"

# Comandos que reescriben el registro o el journal: van bajo cerrojo.
MUTATING = {"open", "decide", "close", "record"}

# Documentos que forman el contexto base. Si cambia alguno, la sesión está desactualizada.
BASE_DOCUMENTS = ("agents/policy/delivery.yaml",)

FALLBACK_PROFILE = {
    "prompt_cache": False,
    # Sincronizado con providers.yaml#parameters.bytes_per_turn: lo comprueba
    # tests/test_session_cli.py#test_fallback_profile_matches_providers.
    "bytes_per_turn": 6_000,
    "bytes_per_token": 4,
    "reuse_margin": 0.9,
    "context_window_tokens": 200_000,
    "loaded_fraction": 0.35,
    "default_turns_per_task": 6,
    "max_tasks_ceiling": 10,
    "max_turns_ceiling": 80,
    "max_age_hours": 24,
}


def load_profile(name: str | None) -> tuple[str, dict]:
    """Devuelve (nombre, parámetros). Nunca falla: si algo falta, aplica el conservador."""
    try:
        data = yaml.safe_load(PROVIDERS.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return "conservative", dict(FALLBACK_PROFILE)

    profiles = data.get("profiles") or {}
    chosen = name or data.get("default_profile") or "conservative"
    profile = profiles.get(chosen)
    if not isinstance(profile, dict):
        chosen, profile = "conservative", profiles.get("conservative") or {}

    params = dict(FALLBACK_PROFILE)
    params.update(data.get("parameters") or {})
    params.update(profile)
    params.setdefault("prompt_cache", False)
    return chosen, params


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def spec_path(raw: str) -> pathlib.Path:
    """Resuelve la especificación: tal cual si existe (CWD), si no contra la raíz del producto.

    El orquestador puede lanzar el CLI desde la raíz del producto o desde `specs/`; las dos
    formas tienen que apuntar al mismo fichero y producir la misma sesión.
    """
    given = pathlib.Path(raw)
    if given.is_absolute():
        return given
    return given.resolve() if given.exists() else (PROJECT_ROOT / given).resolve()


def base_documents(role: str, spec: str) -> list[tuple[str, pathlib.Path]]:
    """(etiqueta estable para la huella, ruta real) de los documentos base.

    La etiqueta no depende del directorio de instalación: mover el repositorio o ejecutar desde
    otro sitio no puede cambiar la huella y forzar una rotación sin que nada haya cambiado.
    """
    labels = [f"agents/cards/{role}.yaml", f"agents/roles/{role}.yaml", *BASE_DOCUMENTS]
    documents = [(label, PROJECT_ROOT / label) for label in labels]
    resolved = spec_path(spec)
    documents.append((str(resolved), resolved))
    return documents


def digest(role: str, spec: str) -> str:
    hasher = hashlib.sha256()
    for label, path in base_documents(role, spec):
        hasher.update(label.encode("utf-8"))
        hasher.update(path.read_bytes() if path.is_file() else b"<ausente>")
    return hasher.hexdigest()


def context_bytes(role: str, spec: str, turns: int, packet_bytes: int, per_turn: int) -> int:
    """Contexto cargado, calculado: tamaño real de los documentos más el coste de los turnos."""
    size = 0
    for _, path in base_documents(role, spec):
        if path.is_file():
            size += path.stat().st_size
    return size + max(0, packet_bytes) + max(0, turns) * per_turn


def costs(base: int, history: int, turns: int, delta: int, profile: dict) -> tuple[float, float]:
    """Coste relativo de (reutilizar, rotar) en las mismas unidades: bytes facturados.

    Sin caché, reutilizar arrastra el historial en cada turno y rotar paga una lectura extra.
    Con caché de prefijo, el prefijo reutilizado se lee con descuento y rotar paga una escritura.
    """
    if profile.get("prompt_cache"):
        read = float(profile.get("read_factor", 0.1))
        write = float(profile.get("write_factor", 1.25))
        reuse = turns * (read * (base + history) + delta)
        rotate = write * base + turns * (read * base + delta)
    else:
        reuse = turns * (base + history + delta)
        rotate = (turns + 1) * base + turns * delta
    return reuse, rotate


def limits(profile: dict, base: int, per_turn: int, window_tokens: int | None = None) -> dict:
    """Topes derivados de la ventana declarada. No hay números fijos que ajustar a mano.

    La ventana es un dato declarado, no consultado: no se pide a ninguna API, se escribe al abrir
    la sesión. De ella salen los bytes que el sistema se permite cargar; de esos bytes, los turnos
    que caben; de esos turnos y de `default_turns_per_task`, las tareas que caben. Los techos del
    perfil recortan el resultado: no son límites de capacidad sino de coherencia.

    El divisor de tareas es el valor declarado, no la media de la sesión: si dependiera de la
    media, cerrar una tarea barata alargaría la sesión y cerrar una cara la cortaría en el acto.
    """
    per_token = max(1, int(profile.get("bytes_per_token", 4)))
    window = int(window_tokens or profile.get("context_window_tokens", 200_000))
    fraction = float(profile.get("loaded_fraction", 0.35))
    loaded = int(window * fraction * per_token)
    allowance = max(0, loaded - max(0, base))

    capacity_turns = allowance // max(1, per_turn)
    max_turns = max(1, min(int(profile.get("max_turns_ceiling", 80)), capacity_turns))

    per_task = max(1, int(profile.get("default_turns_per_task", 6)))
    max_tasks = max(1, min(int(profile.get("max_tasks_ceiling", 10)), max_turns // per_task))

    return {
        "window_tokens": window,
        "max_loaded_bytes": loaded,
        "max_turns": max_turns,
        "max_tasks": max_tasks,
        "turns_per_task": per_task,
        "history_allowance": allowance,
    }


def journal_path(registry: pathlib.Path) -> pathlib.Path:
    return registry.parent / "sessions.jsonl"


def append_event(registry: pathlib.Path, event: dict) -> None:
    event["ts"] = iso(now())
    path = journal_path(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + chr(10))


def read_events(registry: pathlib.Path, role: str, since: datetime | None = None) -> list[dict]:
    path = journal_path(registry)
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("role") != role:
            continue
        moment = parse(event.get("ts"))
        if since and moment and moment < since:
            continue
        events.append(event)
    return events


def summarise(events: list[dict]) -> dict:
    return {
        "turns": sum(1 for event in events if event.get("type") == "turn"),
        "read": sum(int(event.get("bytes") or 0) for event in events if event.get("type") == "read"),
        "write": sum(int(event.get("bytes") or 0) for event in events if event.get("type") == "write"),
    }


def load(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"$schema_version": 1, "updated_at": None, "sessions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: pathlib.Path, data: dict) -> None:
    data["$schema_version"] = 1
    data["updated_at"] = iso(now())
    path.parent.mkdir(parents=True, exist_ok=True)
    # Nombre único por proceso: aunque el cerrojo se saltara, dos escritores no comparten temporal.
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    temporary.replace(path)


@contextlib.contextmanager
def locked(registry: pathlib.Path, timeout: float = 10.0):
    """Exclusión mutua entre procesos para el registro y su journal.

    El registro se reescribe entero (leer-modificar-escribir): sin cerrojo, dos procesos que
    cierran tareas a la vez se pisan y una de las dos actualizaciones desaparece. El journal es
    append-only, pero se serializa igual para no entrelazar líneas a medio escribir.

    El cerrojo es un fichero hermano del registro (`sessions.json.lock`) y se adquiere sin
    bloquear, reintentando hasta agotar el plazo: si algo se queda colgado, el CLI falla con un
    mensaje en vez de esperar para siempre.
    """
    lock_path = registry.with_name(registry.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        _acquire(handle, timeout)
        try:
            yield
        finally:
            _release(handle)
    finally:
        handle.close()


def _acquire(handle, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no se pudo bloquear {handle.name} en {timeout:g} s") from None
            time.sleep(0.05)


def _release(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def known_roles() -> set[str]:
    """Roles declarados en agents/registry.json. Vacío si el fichero no se puede leer."""
    try:
        data = json.loads(ROLES_REGISTRY.read_text(encoding="utf-8"))
        return {str(entry["id"]) for entry in data.get("roles", []) if "id" in entry}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def read_packet(path: str) -> tuple[dict | None, str | None]:
    file = pathlib.Path(path)
    if not file.is_file():
        return None, f"no encuentro el packet '{path}'"
    try:
        packet = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"packet ilegible '{path}': {exc}"
    if not isinstance(packet, dict):
        return None, f"el packet '{path}' no es un objeto JSON"
    return packet, None


def memory_max_bytes() -> int:
    """Tope de tamaño de la memoria de rol, declarado en sessions.yaml#memory."""
    try:
        data = yaml.safe_load(SESSIONS_POLICY.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return 8_192
    memory = data.get("memory") or {}
    value = memory.get("max_bytes")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 8_192


def memory_warnings(path: pathlib.Path, opened: datetime | None, limit: int) -> list[str]:
    """Avisos sobre la memoria de rol: existe, está al día y cabe en el presupuesto."""
    if not path.is_file():
        return [f"no encuentro la memoria '{path}': la siguiente sesión arrancará sin ella"]
    messages: list[str] = []
    size = path.stat().st_size
    if size > limit:
        messages.append(f"la memoria ocupa {size:,} B (tope {limit:,}): poda lo que ya no sea cierto")
    if opened:
        moment = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if moment < opened:
            messages.append("la memoria no se ha actualizado desde que abrió la sesión")
    return messages


def checks(role: str, spec: str) -> str | None:
    """Errores de uso que no admiten decisión: rol inexistente o especificación ausente."""
    roles = known_roles()
    if roles and role not in roles:
        return f"el rol '{role}' no está en agents/registry.json"
    resolved = spec_path(spec)
    if not resolved.is_file():
        return (
            f"no encuentro la especificación '{spec}' (resuelta a '{resolved}'): una sesión sin"
            " especificación no puede detectar que la especificación cambió"
        )
    return None


def find(data: dict, role: str) -> dict | None:
    for entry in data.get("sessions", []):
        if entry.get("role") == role:
            return entry
    return None


def cmd_open(args: argparse.Namespace) -> int:
    error = checks(args.role, args.spec)
    if error:
        print(error, file=sys.stderr)
        return 2
    resolved_spec = str(spec_path(args.spec))
    registry = pathlib.Path(args.registry)
    data = load(registry)
    profile_name, profile = load_profile(args.profile or data.get("profile"))
    data["profile"] = profile_name
    previous = find(data, args.role)
    if previous and previous.get("tasks_closed"):
        closed = previous["tasks_closed"]
        print(
            f"aviso: se descarta la sesión anterior de {args.role}"
            f" ({len(closed)} tareas cerradas: {', '.join(closed)}); no se puede reabrir"
        )
    data["sessions"] = [entry for entry in data.get("sessions", []) if entry.get("role") != args.role]
    data["sessions"].append(
        {
            "role": args.role,
            "model": args.model,
            "session_id": args.session_id or f"{args.role}-{int(now().timestamp())}",
            "domain": args.domain,
            "spec": resolved_spec,
            "profile": profile_name,
            "window_tokens": int(args.window or profile.get("context_window_tokens", 200_000)),
            "inputs_hash": digest(args.role, resolved_spec),
            "tasks_closed": [],
            "turns": 0,
            "loaded_bytes": 0,
            "opened_at": iso(now()),
            "updated_at": iso(now()),
            "rotate_reason": None,
        }
    )
    save(registry, data)
    print(
        f"sesión abierta: {args.role} · dominio {args.domain} · {resolved_spec}"
        f" · perfil {profile_name} · ventana {data['sessions'][-1]['window_tokens']:,} tokens"
    )
    return 0


def packet_facts(path: str | None, role: str, spec: str) -> tuple[int, bool, str | None]:
    """Lo que la decisión necesita del packet, sin leerlo dos veces.

    Devuelve (bytes del packet, si exige revisión independiente, error de uso). El packet es la
    única descripción de la tarea que ve el worker: si no coincide con el rol o con la
    especificación declarados, la decisión se estaría tomando sobre dos tareas distintas.
    """
    if not path:
        return 0, False, None
    packet, error = read_packet(path)
    if error:
        return 0, False, error

    if packet.get("role") != role:
        return 0, False, f"el packet es del rol '{packet.get('role')}', no de '{role}'"

    declared_spec = packet.get("spec")
    try:
        same_spec = spec_path(str(declared_spec)) == spec_path(spec)
    except (OSError, ValueError, TypeError):
        same_spec = declared_spec == spec
    if not same_spec:
        return 0, False, f"el packet es de la especificación '{declared_spec}', no de '{spec}'"

    return pathlib.Path(path).stat().st_size, bool(packet.get("independent")), None


def cmd_decide(args: argparse.Namespace) -> int:
    error = checks(args.role, args.spec)
    if error:
        print(error, file=sys.stderr)
        return 2

    spec = str(spec_path(args.spec))
    packet_bytes, packet_independent, packet_error = packet_facts(args.packet, args.role, spec)
    if packet_error:
        print(packet_error, file=sys.stderr)
        return 2
    if not args.packet:
        packet_bytes = max(0, args.packet_bytes)

    registry = pathlib.Path(args.registry)
    data = load(registry)
    entry = find(data, args.role)

    def journal(outcome: str, reason: str) -> None:
        append_event(
            registry,
            {
                "type": "decision",
                "role": args.role,
                "session": (entry or {}).get("session_id"),
                "spec": spec,
                "outcome": outcome,
                "reason": reason,
            },
        )

    def rotate(reason: str) -> int:
        journal("ROTATE", reason)
        if entry is not None:
            entry["rotate_reason"] = reason
            save(registry, data)
        print(f"ROTATE · {reason}")
        if entry is None:
            print(
                "  no hay sesión registrada: ábrela con `session.py open` y vuelve a preguntar."
                " Sin historial no hay handoff que escribir."
            )
        else:
            print(
                "  antes de cerrar: handoff de 10 líneas y memoria del rol al día;"
                " la sesión nueva arranca con card, memoria y tarea."
            )
        return 10

    if entry is None:
        return rotate("no hay sesión registrada para este rol")

    closed = entry.get("tasks_closed") or []
    if args.continuation and args.continuation not in closed:
        return rotate(f"la tarea {args.continuation} no la ha trabajado esta sesión: no es continuación")

    # Legalidad. Modelo y dominio son obligatorios en la línea de órdenes justo para que estas
    # comprobaciones existan: omitir un argumento no puede desactivar un control.
    if entry.get("model") != args.model:
        return rotate(f"cambia el modelo ({entry.get('model')} → {args.model})")

    if entry.get("domain") != args.domain:
        return rotate(f"cambia el dominio ({entry.get('domain')} → {args.domain})")

    if args.independent or packet_independent:
        return rotate("la tarea exige revisión independiente")

    if entry.get("spec") != spec:
        return rotate(f"cambia la especificación ({entry.get('spec')} → {spec})")

    if entry.get("inputs_hash") != digest(args.role, spec):
        return rotate("cambiaron los documentos base (card, rol, especificación o política)")

    profile_name, profile = load_profile(args.profile or entry.get("profile") or data.get("profile"))
    per_turn = int(profile.get("bytes_per_turn", 6_000))

    base = context_bytes(args.role, spec, 0, 0, per_turn)
    observed = round((entry.get("turns") or 0) / len(closed)) if closed else 0
    caps = limits(profile, base, per_turn, args.window or entry.get("window_tokens"))

    max_age = float(profile.get("max_age_hours", 24))
    opened = parse(entry.get("opened_at"))
    if opened and now() - opened > timedelta(hours=max_age):
        age = (now() - opened).total_seconds() / 3600
        return rotate(f"la sesión tiene {age:.1f} h (máximo {max_age:g} h)")

    if len(closed) >= caps["max_tasks"]:
        return rotate(
            f"{len(closed)} tareas cerradas (máximo {caps['max_tasks']} para"
            f" ventana {caps['window_tokens']:,} tokens y {caps['turns_per_task']} turnos por tarea)"
        )

    if (entry.get("turns") or 0) >= caps["max_turns"]:
        return rotate(
            f"{entry['turns']} turnos (máximo {caps['max_turns']} derivado de"
            f" {caps['max_loaded_bytes']:,} B de tope)"
        )

    history = (entry.get("turns") or 0) * per_turn
    loaded = context_bytes(args.role, spec, entry.get("turns") or 0, packet_bytes, per_turn)

    if loaded > caps["max_loaded_bytes"]:
        return rotate(
            f"contexto cargado {loaded:,} B (tope {caps['max_loaded_bytes']:,} B ="
            f" {caps['window_tokens']:,} tokens × fracción declarada)"
        )

    # Estado de la caché: solo se considera si el perfil declara esa capacidad.
    if not profile.get("prompt_cache"):
        warm = False
        cache_note = f"perfil {profile_name} sin caché de prefijo"
    elif args.cache == "warm":
        warm, cache_note = True, f"perfil {profile_name}, caché forzada"
    elif args.cache == "cold":
        warm, cache_note = False, f"perfil {profile_name}, caché forzada a fría"
    else:
        updated = parse(entry.get("updated_at"))
        ttl = int(profile.get("ttl_minutes", 30))
        warm = bool(updated) and now() - updated <= timedelta(minutes=ttl)
        cache_note = f"perfil {profile_name}, caché {'caliente' if warm else 'fría'} (TTL {ttl} min)"

    delta = max(0, packet_bytes) + per_turn
    expected = args.expected_turns or max(1, observed)
    margin = float(profile.get("reuse_margin", 0.9))

    reuse_cost, rotate_cost = costs(base, history, expected, delta, {**profile, "prompt_cache": warm})
    if reuse_cost >= rotate_cost * margin:
        return rotate(
            f"no compensa: reutilizar costaría {reuse_cost:,.0f} B y rotar {rotate_cost:,.0f} B"
            f" para {expected} turnos · {cache_note}"
        )

    mode = (
        f"continuación de {args.continuation}"
        if args.continuation
        else "seguimiento del mismo PR"
        if args.follow_up
        else "reutilización sin intención declarada"
    )
    journal("REUSE", f"{mode} · {cache_note}")
    if entry.get("rotate_reason"):
        entry["rotate_reason"] = None
    save(registry, data)

    print(f"REUSE · {mode}")
    print(
        f"  coste estimado: reutilizar {reuse_cost:,.0f} B vs rotar {rotate_cost:,.0f} B"
        f" para {expected} turnos · {cache_note}"
    )
    print(f"  sesión {entry.get('session_id')} · tareas {len(closed)}/{caps['max_tasks']} · turnos {entry.get('turns') or 0}/{caps['max_turns']}")
    print(
        f"  ancla {str(entry.get('inputs_hash'))[:12]} · documentos base sin cambios"
        f" desde {entry.get('opened_at')}"
    )
    print(
        f"  margen: {caps['max_loaded_bytes'] - loaded:,} B de {caps['max_loaded_bytes']:,} B"
        f" (ventana declarada {caps['window_tokens']:,} tokens)"
    )
    print("  enviar solo:")
    print("    - tarea: identificador, título y packet")
    print("    - memoria del rol, si cambió")
    print("    - lista de ficheros tocados, sin contenido")
    print(
        "  no enviar: documentos base ni especificación — su huella no ha cambiado"
        " (si hubiera cambiado, la respuesta habría sido ROTATE)."
    )
    print("  no rellenar con memoria: si la especificación no dice lo que la sesión recuerda, para y pregunta.")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    registry = pathlib.Path(args.registry)
    data = load(registry)
    entry = find(data, args.role)
    if entry is None:
        print("no hay sesión registrada para este rol", file=sys.stderr)
        return 1

    if args.board:
        board_file = pathlib.Path(args.board)
        if not board_file.is_file():
            print(f"no encuentro el tablero '{args.board}'", file=sys.stderr)
            return 2
        try:
            board = json.loads(board_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"no se puede leer el tablero: {exc}", file=sys.stderr)
            return 2
        tasks = board.get("tasks") if isinstance(board, dict) else None
        found = None
        if isinstance(tasks, list):
            found = next(
                (
                    item
                    for item in tasks
                    if isinstance(item, dict) and item.get("task") == args.task
                ),
                None,
            )
        if found is None:
            print(f"la tarea '{args.task}' no está en el tablero '{args.board}'", file=sys.stderr)
            return 1
        owner = found.get("owner")
        if owner != args.role:
            print(f"la tarea '{args.task}' es de '{owner}', no de '{args.role}'", file=sys.stderr)
            return 1
        state = found.get("status")
        if state not in {"in_progress", "review", "done"}:
            print(
                f"la tarea '{args.task}' está en '{state}': no se cierra lo que no está en curso",
                file=sys.stderr,
            )
            return 1
        board_spec = found.get("spec")
        if isinstance(board_spec, str) and board_spec:
            if spec_path(board_spec) != spec_path(entry.get("spec", "")):
                print(
                    f"el tablero apunta a '{board_spec}' y la sesión a '{entry.get('spec')}':"
                    " no se cierra sobre dos especificaciones",
                    file=sys.stderr,
                )
                return 1

    since = parse(entry.get("opened_at"))
    summary = summarise(read_events(registry, args.role, since))
    journal_turns = summary["turns"]
    bytes_read = summary["read"]
    bytes_written = summary["write"]

    closed = entry.setdefault("tasks_closed", [])
    repeated = args.task in closed
    if not repeated:
        closed.append(args.task)

    stored = entry.get("turns") or 0
    if journal_turns > 0:
        # El journal es acumulado de la sesión y manda: cerrar con --turns no puede reducirlo.
        total, source = journal_turns, "journal"
    elif repeated:
        total, source = stored, "declared"  # cerrar dos veces la misma tarea no suma turnos
    else:
        total, source = stored + (args.turns or 0), "declared"

    elapsed = int((now() - since).total_seconds()) if since else 0
    if source == "declared" and total > elapsed // 2 + 1:
        print(f"aviso: {total} turnos para {elapsed} s de sesión; se usa lo declarado, revisa la medida")

    _, profile = load_profile(entry.get("profile") or data.get("profile"))
    per_turn = int(profile.get("bytes_per_turn", 6_000))
    base = context_bytes(args.role, entry.get("spec", ""), 0, 0, per_turn)

    task_turns = 0 if repeated else max(0, total - stored)
    entry["turns"] = total
    entry["updated_at"] = iso(now())
    entry["turns_source"] = source
    entry["bytes_read_reported"] = bytes_read
    entry["bytes_written_reported"] = bytes_written
    # Una sola fórmula de presupuesto: documentos + turnos + packet. Los bytes leídos se guardan
    # aparte porque son auditoría del trabajo, no tamaño del contexto.
    entry["loaded_bytes"] = context_bytes(args.role, entry.get("spec", ""), total, 0, per_turn)

    caps = limits(profile, base, per_turn, entry.get("window_tokens"))

    if args.memory:
        for warning in memory_warnings(pathlib.Path(args.memory), since, memory_max_bytes()):
            print(f"aviso: {warning}")

    save(registry, data)
    print(
        f"sesión actualizada: {args.role} · {len(closed)} tareas · {total} turnos acumulados"
        f" ({source}, +{task_turns} en {args.task}) · {entry['loaded_bytes']} B de contexto"
        f" · tope {caps['max_loaded_bytes']:,} B ({caps['window_tokens']:,} tokens)"
    )
    return 0 if len(closed) < caps["max_tasks"] else 10


def cmd_record(args: argparse.Namespace) -> int:
    registry = pathlib.Path(args.registry)
    data = load(registry)
    entry = find(data, args.role)
    roles = known_roles()
    if entry is None and roles and args.role not in roles:
        print(f"el rol '{args.role}' no está en agents/registry.json", file=sys.stderr)
        return 2
    event = {
        "type": args.event,
        "role": args.role,
        "session": args.session or (entry or {}).get("session_id"),
    }
    if args.event == "turn":
        event["n"] = args.n or len([e for e in read_events(registry, args.role) if e.get("type") == "turn"]) + 1
    if args.path:
        event["path"] = args.path
    if args.nbytes is not None:
        event["bytes"] = args.nbytes
    if args.text:
        event["text"] = args.text
    append_event(registry, event)
    if entry is not None:
        entry["updated_at"] = iso(now())
        save(registry, data)
    print(f"registrado: {args.event} · {args.role}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    registry = pathlib.Path(args.registry)
    data = load(registry)
    rows = []
    for entry in data.get("sessions", []):
        closed = entry.get("tasks_closed") or []
        if not closed:
            continue
        turns = entry.get("turns") or 0
        journal = summarise(read_events(registry, entry.get("role", ""), parse(entry.get("opened_at"))))["turns"]
        rows.append(
            (
                entry.get("role", "?"),
                len(closed),
                turns,
                round(turns / len(closed)),
                journal,
                entry.get("loaded_bytes") or 0,
            )
        )
    if not rows:
        print("sin tareas cerradas con las que calibrar")
        return 0
    print(f"{'rol':<22} {'tareas':>6} {'turnos':>7} {'media':>6} {'journal':>8} {'contexto':>9}")
    for role, tasks, turns, average, journal, loaded in rows:
        print(f"{role:<22} {tasks:>6} {turns:>7} {average:>6} {journal:>8} {loaded:>9}")
    print()
    if any(turns != journal for _, _, turns, _, journal, _ in rows):
        print("aviso: turnos declarados y registrados difieren; al cerrar manda el journal.")
    print("Sugerencia: usa la media de cada rol como --expected-turns y ajusta bytes_per_turn en el perfil.")
    return 0


def git_output(*arguments: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def worktree_paths() -> set[str]:
    """Rutas con cambios sin confirmar o sin seguimiento: un diff de ramas no las ve.

    Solo tiene sentido sobre el árbol de trabajo de la rama actual; quien verifica otra rama
    compara contra su referencia, no contra lo que tenga a medio editar en local.
    """
    code, out, _ = git_output(
        "-c", "core.quotePath=false", "status", "--porcelain", "-z", "--untracked-files=all"
    )
    if code != 0:
        return set()
    paths: set[str] = set()
    entries = out.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        state, path = entry[:2], entry[3:]
        paths.add(path.replace("\\", "/"))
        # En -z, un renombrado o copia trae el origen en la entrada siguiente: no es escritura nueva.
        if "R" in state or "C" in state:
            index += 1
    return paths


def cmd_check(args: argparse.Namespace) -> int:
    """Valida el packet antes de repartir trabajo: estructura, especificación y permisos."""
    packet, error = read_packet(args.packet)
    if error:
        print(error, file=sys.stderr)
        return 2

    role = packet.get("role")
    if not isinstance(role, str) or not role:
        print("el packet no declara role", file=sys.stderr)
        return 2
    roles = known_roles()
    if roles and role not in roles:
        print(f"el rol '{role}' no está en agents/registry.json", file=sys.stderr)
        return 2

    spec = packet.get("spec")
    if not isinstance(spec, str) or not spec:
        print("el packet no declara spec", file=sys.stderr)
        return 2
    if not spec_path(spec).is_file():
        print(f"no encuentro la especificación '{spec}'", file=sys.stderr)
        return 2

    patterns = packet.get("write")
    if not isinstance(patterns, list) or not patterns:
        print("el packet no declara write", file=sys.stderr)
        return 2

    gates = packet.get("gates")
    if gates is not None and not isinstance(gates, list):
        print("el packet declara gates, pero no como lista", file=sys.stderr)
        return 2

    errors = permission_errors(packet, role)
    if errors:
        print(
            f"{len(errors)} permiso(s) fuera de la matriz de agents/permissions.json:",
            file=sys.stderr,
        )
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(
        f"packet válido: {role} · {len(patterns)} patrones de escritura"
        f" · {len(packet.get('categories') or {})} categorías dentro de la matriz"
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Compara el diff real de la rama con el write set del packet.

    Requisitos para que la comprobación signifique algo:
      - se compara la rama del PR, no el árbol donde vive el orquestador;
      - se ven también los cambios sin confirmar y los ficheros sin seguimiento;
      - un diff vacío es un error, no una tarea limpia.
    """
    packet, error = read_packet(args.packet)
    if error:
        print(error, file=sys.stderr)
        return 2

    role = packet.get("role")
    if not isinstance(role, str) or not role:
        print("el packet no declara role", file=sys.stderr)
        return 2
    violations = permission_errors(packet, role)
    if violations:
        print(
            f"{len(violations)} permiso(s) fuera de la matriz de agents/permissions.json:",
            file=sys.stderr,
        )
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        return 1

    patterns = packet.get("write") or []
    if not patterns:
        print("el packet no declara write", file=sys.stderr)
        return 2
    compiled = [path_pattern(str(pattern)) for pattern in patterns]

    code, out, _ = git_output("rev-parse", "--abbrev-ref", "HEAD")
    current = out.strip() if code == 0 else ""
    branch = args.branch or current
    if not branch or branch == "HEAD":
        print("rama indeterminada: pasa --branch <rama del PR>", file=sys.stderr)
        return 2

    code, out, err = git_output(
        "-c", "core.quotePath=false", "diff", "--name-only", "-z", f"{args.base}...{branch}"
    )
    if code != 0:
        print(f"no se pudo comparar {args.base}...{branch}: {err.strip()}", file=sys.stderr)
        return 2

    committed = {path for path in out.split("\0") if path}
    uncommitted = worktree_paths() if branch == current else set()
    changed = sorted(committed | uncommitted)

    print(
        f"rama {branch} · base {args.base} · {len(committed)} confirmados"
        f" · {len(uncommitted)} sin confirmar"
    )

    if not changed:
        if args.allow_empty:
            print("sin cambios: se acepta por --allow-empty")
            return 0
        print(
            f"no hay cambios entre {args.base} y {branch}: revisa la rama antes de dar la tarea"
            " por verificada (un diff vacío no es una tarea limpia)",
            file=sys.stderr,
        )
        return 2

    outside = [path for path in changed if not any(pattern.match(path) for pattern in compiled)]
    print(f"{len(changed)} ficheros cambiados · {len(outside)} fuera del write set del packet")
    for path in outside:
        print(f"  fuera: {path}")
    return 1 if outside else 0


def cmd_state(args: argparse.Namespace) -> int:
    registry = pathlib.Path(args.registry)
    data = load(registry)
    sessions = data.get("sessions", [])
    if args.role:
        sessions = [entry for entry in sessions if entry.get("role") == args.role]
    if not sessions:
        print("sin sesiones registradas")
        return 0
    for entry in sessions:
        _, profile = load_profile(entry.get("profile") or data.get("profile"))
        per_turn = int(profile.get("bytes_per_turn", 6_000))
        closed = entry.get("tasks_closed") or []
        base = context_bytes(entry.get("role", ""), entry.get("spec", ""), 0, 0, per_turn)
        loaded = context_bytes(entry.get("role", ""), entry.get("spec", ""), entry.get("turns") or 0, 0, per_turn)
        caps = limits(profile, base, per_turn, entry.get("window_tokens"))
        print(
            f"{entry.get('role'):<22} {entry.get('model', '-'):<18}"
            f" tareas {len(closed)}/{caps['max_tasks']} · turnos {entry.get('turns') or 0}/{caps['max_turns']}"
            f" · {loaded:,}/{caps['max_loaded_bytes']:,} B · {entry.get('domain')}"
            f" · perfil {entry.get('profile')} · ventana {caps['window_tokens']:,}"
        )
        details: list[str] = []
        decisions = [
            event
            for event in read_events(registry, entry.get("role", ""))
            if event.get("type") == "decision"
        ]
        if decisions:
            last = decisions[-1]
            details.append(f"última decisión: {last.get('outcome')} · {last.get('reason')}")
        elif entry.get("rotate_reason"):
            # Sin journal (borrado o nunca escrito) el registro es la única pista del motivo.
            details.append(f"rotar: {entry['rotate_reason']}")
        if details:
            print("    " + " · ".join(details))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Política de sesiones ejecutable.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--profile", default=None, help="perfil de agents/policy/providers.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("--role", required=True)
    open_parser.add_argument("--model", required=True)
    open_parser.add_argument("--domain", required=True)
    open_parser.add_argument("--spec", required=True)
    open_parser.add_argument("--session-id", default=None)
    open_parser.add_argument("--window", type=int, default=None, help="ventana de contexto en tokens")
    open_parser.set_defaults(handler=cmd_open)

    decide_parser = subparsers.add_parser("decide")
    decide_parser.add_argument("--role", required=True)
    decide_parser.add_argument("--spec", required=True)
    # Obligatorios a propósito: si faltan, las comprobaciones de modelo y dominio no existirían.
    decide_parser.add_argument("--model", required=True)
    decide_parser.add_argument("--domain", required=True)
    decide_parser.add_argument("--packet", default=None, help="packet de la tarea; aporta tamaño y revisión independiente")
    decide_parser.add_argument("--packet-bytes", type=int, default=0)
    decide_parser.add_argument("--independent", action="store_true")
    decide_parser.add_argument("--continuation", default=None)
    decide_parser.add_argument("--follow-up", action="store_true")
    decide_parser.add_argument("--expected-turns", type=int, default=None)
    decide_parser.add_argument("--window", type=int, default=None, help="ventana de contexto en tokens")
    decide_parser.add_argument("--cache", choices=["auto", "warm", "cold"], default="auto")
    decide_parser.set_defaults(handler=cmd_decide)

    close_parser = subparsers.add_parser("close")
    close_parser.add_argument("--role", required=True)
    close_parser.add_argument("--task", required=True)
    close_parser.add_argument("--turns", type=int, default=0)
    close_parser.add_argument("--memory", default=None, help="memoria del rol: se avisa si falta, está vieja o no cabe")
    close_parser.add_argument("--board", default=None, help="tablero: comprueba tarea, dueño, estado y especificación antes de cerrar")
    close_parser.set_defaults(handler=cmd_close)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--packet", required=True)
    check_parser.set_defaults(handler=cmd_check)

    state_parser = subparsers.add_parser("state")
    state_parser.add_argument("--role", default=None)
    state_parser.set_defaults(handler=cmd_state)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--role", required=True)
    record_parser.add_argument("--event", required=True, choices=["turn", "read", "write", "note"])
    record_parser.add_argument("--path", default=None)
    record_parser.add_argument("--bytes", dest="nbytes", type=int, default=None)
    record_parser.add_argument("--text", default=None)
    record_parser.add_argument("--n", type=int, default=None)
    record_parser.add_argument("--session", default=None)
    record_parser.set_defaults(handler=cmd_record)

    stats_parser = subparsers.add_parser("stats")
    stats_parser.set_defaults(handler=cmd_stats)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--packet", required=True)
    verify_parser.add_argument("--base", default="origin/main")
    verify_parser.add_argument("--branch", default=None, help="rama del PR; por defecto, la actual")
    verify_parser.add_argument("--allow-empty", action="store_true", help="acepta un diff vacío")
    verify_parser.set_defaults(handler=cmd_verify)

    args = parser.parse_args()
    if args.command in MUTATING:
        try:
            with locked(pathlib.Path(args.registry)):
                return args.handler(args)
        except TimeoutError as exc:
            print(f"registro ocupado: {exc}", file=sys.stderr)
            return 1
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
