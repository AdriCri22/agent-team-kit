"""Valida el sistema de agentes: formato, coherencia y permisos.

Uso: python3 agents/tools/validate.py

Comprueba:
  1. Todo .yaml del repo parsea.
  2. Los roles tienen las claves obligatorias y el id coincide con el nombre del fichero.
  3. roles/, cards/, registry.json y permissions.json describen exactamente el mismo equipo.
  4. La jerarquía no tiene ciclos y reports_to apunta a un rol existente.
  5. La matriz de permisos cubre todas las categorías en todos los roles y es coherente con
     las reglas declaradas (sin código para producto/diseño, sin código para calidad).
  6. Los enlaces relativos de los .md resuelven.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLES = ROOT / "agents" / "roles"
CARDS = ROOT / "agents" / "cards"

ROLE_KEYS = [
    "id",
    "name",
    "layer",
    "reports_to",
    "coordinates",
    "reviews",
    "instances",
    "mission",
    "decides",
    "does_not",
    "defends",
    "knowledge",
    "escalates_to",
    "publishes",
    "consumes",
    "definition_of_done",
]
CARD_KEYS = [
    "id",
    "name",
    "layer",
    "reports_to",
    "role",
    "permissions",
    "mission",
    "reading",
    "escalates_to",
]

errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def load_yaml(path: pathlib.Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"{path.relative_to(ROOT)}: YAML inválido — {exc}")
        return None


def run_product(product: pathlib.Path, board: str | None) -> int:
    """Valida una instancia: el sistema que lleva dentro y su tablero.

    Se ejecuta con las herramientas del propio producto, para que `agents/` sea su ancla y no la
    del repositorio de la plantilla.
    """
    if not product.is_dir():
        print(f"no encuentro el producto '{product}'", file=sys.stderr)
        return 2

    tools = product / "agents" / "tools"
    for tool in ("validate.py", "board.py"):
        if not (tools / tool).is_file():
            print(
                f"el producto no contiene agents/tools/{tool}: copia el sistema en su raíz",
                file=sys.stderr,
            )
            return 2

    board_path = pathlib.Path(board) if board else pathlib.Path("backlog.json")
    if not board_path.is_absolute():
        board_path = product / board_path
    if not board_path.is_file():
        print(f"no encuentro el tablero '{board_path}' (usa --board <ruta>)", file=sys.stderr)
        return 2

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    system = subprocess.run(
        [sys.executable, str(tools / "validate.py")],
        cwd=str(product),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    board_run = subprocess.run(
        [sys.executable, str(tools / "board.py"), "lint", "--board", str(board_path)],
        cwd=str(product),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    failed = False
    print(f"producto · {product}")
    for label, result in (("sistema", system), ("tablero", board_run)):
        if result.returncode == 0:
            first = result.stdout.strip().splitlines()
            print(f"  {label} · {first[0] if first else 'OK'}")
            continue
        failed = True
        print(f"  {label} · ERROR")
        for line in (result.stdout or result.stderr).strip().splitlines():
            print(f"    {line}")
    return 1 if failed else 0


parser = argparse.ArgumentParser(
    description="Valida el sistema de agentes o una instancia del producto."
)
parser.add_argument("--product", default=None, help="valida una instancia: sistema y tablero")
parser.add_argument("--board", default=None, help="tablero del producto (por defecto <producto>/backlog.json)")
ARGS = parser.parse_args()
if ARGS.product:
    sys.exit(run_product(pathlib.Path(ARGS.product), ARGS.board))


# 1. Todos los YAML parsean.
for path in ROOT.rglob("*.yaml"):
    if ".git" in path.parts or ".orchestrator" in path.parts:
        continue
    load_yaml(path)

# Los tests de las herramientas viven en tests/, no en agents/tools: esa carpeta es lo que los
# agentes ejecutan y un test ahí se confunde con una herramienta de rol.
for stray in sorted(ROOT.glob("agents/tools/test_*.py")):
    fail(f"{stray.relative_to(ROOT)}: los tests de las herramientas viven en tests/")

# 2. Roles con claves completas.
role_ids: set[str] = set()
for path in sorted(ROLES.glob("*.yaml")):
    data = load_yaml(path)
    if not isinstance(data, dict):
        continue
    missing = [key for key in ROLE_KEYS if key not in data]
    if missing:
        fail(f"roles/{path.name}: faltan claves {missing}")
    if data.get("id") != path.stem:
        fail(f"roles/{path.name}: id '{data.get('id')}' no coincide con el fichero")
    for list_key in ("decides", "does_not", "defends", "knowledge", "coordinates", "reviews"):
        value = data.get(list_key)
        if not isinstance(value, list):
            fail(f"roles/{path.name}: '{list_key}' debe ser lista")
            continue
        for item in value:
            if not isinstance(item, str):
                fail(f"roles/{path.name}: '{list_key}' tiene un ítem que no es texto: {item!r}")
    for list_key in ("publishes", "consumes"):
        value = data.get(list_key)
        if not isinstance(value, list):
            fail(f"roles/{path.name}: '{list_key}' debe ser lista")
            continue
        for item in value:
            if not isinstance(item, dict):
                fail(f"roles/{path.name}: '{list_key}' tiene un ítem que no es mapa: {item!r}")
    role_ids.add(path.stem)

# 3. Cards.
card_ids: set[str] = set()
for path in sorted(CARDS.glob("*.yaml")):
    data = load_yaml(path)
    if not isinstance(data, dict):
        continue
    missing = [key for key in CARD_KEYS if key not in data]
    if missing:
        fail(f"cards/{path.name}: faltan claves {missing}")
    if data.get("id") != path.stem:
        fail(f"cards/{path.name}: id '{data.get('id')}' no coincide con el fichero")
    if data.get("role") != f"agents/roles/{path.stem}.yaml":
        fail(f"cards/{path.name}: 'role' debe apuntar a agents/roles/{path.stem}.yaml")
    card_ids.add(path.stem)

if role_ids != card_ids:
    fail(f"roles y cards no coinciden — solo rol: {role_ids - card_ids}, solo card: {card_ids - role_ids}")

# 3b. Escalado coherente entre rol y card, y destino existente.
VALID_ESCALATION = role_ids | {"quien-encarga"}
for path in sorted(ROLES.glob("*.yaml")):
    data = load_yaml(path)
    if not isinstance(data, dict):
        continue
    target = data.get("escalates_to")
    if isinstance(target, str) and target not in VALID_ESCALATION:
        fail(f"roles/{path.name}: escalates_to '{target}' no existe")
for path in sorted(CARDS.glob("*.yaml")):
    data = load_yaml(path)
    role_path = ROLES / path.name
    if not isinstance(data, dict) or not role_path.exists():
        continue
    role_data = load_yaml(role_path)
    if isinstance(role_data, dict) and data.get("escalates_to") != role_data.get("escalates_to"):
        fail(f"cards/{path.name}: escalates_to no coincide con el rol")

# 4. Registro.
registry = json.loads((ROOT / "agents" / "registry.json").read_text(encoding="utf-8"))
registry_ids = {entry["id"] for entry in registry["roles"]}
if registry_ids != role_ids:
    fail(f"registry.json no coincide — faltan: {role_ids - registry_ids}, sobran: {registry_ids - role_ids}")

by_id = {entry["id"]: entry for entry in registry["roles"]}
for entry in registry["roles"]:
    parent = entry.get("reports_to")
    if parent is not None and parent not in registry_ids:
        fail(f"registry.json: {entry['id']} reporta a '{parent}', que no existe")
    if entry.get("layer") not in {layer["id"] for layer in registry["layers"]}:
        fail(f"registry.json: {entry['id']} tiene capa desconocida '{entry.get('layer')}'")

# La capa del rol y de su card debe coincidir con la del registro.
for path in sorted(ROLES.glob("*.yaml")):
    data = load_yaml(path)
    if not isinstance(data, dict):
        continue
    entry = by_id.get(path.stem)
    if not isinstance(entry, dict):
        continue  # la discrepancia entre registro y roles ya se ha reportado arriba
    if data.get("layer") != entry.get("layer"):
        fail(f"roles/{path.name}: capa '{data.get('layer')}' no coincide con registry ({entry.get('layer')})")
    if data.get("name") != entry.get("name"):
        fail(f"roles/{path.name}: nombre '{data.get('name')}' no coincide con registry ({entry.get('name')})")
    if data.get("reports_to") != entry.get("reports_to"):
        fail(
            f"roles/{path.name}: reports_to '{data.get('reports_to')}' no coincide con registry"
            f" ({entry.get('reports_to')})"
        )
for path in sorted(CARDS.glob("*.yaml")):
    data = load_yaml(path)
    entry = by_id.get(path.stem)
    if isinstance(data, dict) and isinstance(entry, dict) and data.get("layer") != entry.get("layer"):
        fail(f"cards/{path.name}: capa '{data.get('layer')}' no coincide con registry")

for start in registry_ids:
    seen: list[str] = []
    current = start
    while current is not None:
        if current in seen:
            fail(f"jerarquía con ciclo: {' → '.join(seen + [current])}")
            break
        seen.append(current)
        entry = by_id.get(current)
        if not isinstance(entry, dict):
            break  # destino inexistente: ya se ha reportado arriba
        current = entry.get("reports_to")

for flow_name, flow in registry["flows"].items():
    for step in flow["steps"]:
        if step["role"] not in registry_ids:
            fail(f"flujo '{flow_name}' referencia rol inexistente '{step['role']}'")

# 4b. Referencias entre roles: id, patrón que case con alguno, '*' o 'external:<actor>'.
ACTORS = set(registry.get("actors") or [])
if not ACTORS:
    fail("registry.json: falta 'actors' (vocabulario de interlocutores externos)")


def reference_error(ref, allow_star: bool) -> str | None:
    if not isinstance(ref, str) or not ref.strip():
        return f"referencia vacía o no textual ({ref!r})"
    if ref == "*":
        return None if allow_star else "'*' solo vale en reviews"
    if ref.startswith("external:"):
        actor = ref.split(":", 1)[1]
        if not allow_star:
            return "reviews no admite actores externos"
        if actor not in ACTORS:
            return f"actor externo '{actor}' no declarado en registry.json#actors"
        return None
    if "*" in ref:
        if not any(fnmatch.fnmatch(role, ref) for role in role_ids):
            return f"patrón '{ref}' no casa con ningún rol"
        return None
    if ref not in role_ids:
        return f"rol '{ref}' no existe"
    return None


for path in sorted(ROLES.glob("*.yaml")):
    data = load_yaml(path)
    if not isinstance(data, dict):
        continue
    for item in data.get("consumes") or []:
        if not isinstance(item, dict):
            continue
        refs = item.get("from")
        if not isinstance(refs, list) or not refs:
            fail(f"roles/{path.name}: consumes.from debe ser lista no vacía (es {refs!r})")
            continue
        for ref in refs:
            problem = reference_error(ref, allow_star=True)
            if problem:
                fail(f"roles/{path.name}: consumes.from → {problem}")
    for field in ("reviews", "coordinates"):
        for ref in data.get(field) or []:
            problem = reference_error(ref, allow_star=field == "reviews")
            if problem:
                fail(f"roles/{path.name}: {field} → {problem}")

# 5. Permisos.
permissions = json.loads((ROOT / "agents" / "permissions.json").read_text(encoding="utf-8"))
categories = set(permissions["categories"])
matrix = permissions["matrix"]
if set(matrix) != role_ids:
    fail(f"permissions.json no coincide — faltan: {role_ids - set(matrix)}, sobran: {set(matrix) - role_ids}")

for role, row in matrix.items():
    missing = categories - set(row)
    if missing:
        fail(f"permissions.json: {role} sin categorías {sorted(missing)}")
    extra = set(row) - categories
    if extra:
        fail(f"permissions.json: {role} con categorías desconocidas {sorted(extra)}")
    for category, level in row.items():
        if level not in permissions["levels"]:
            fail(f"permissions.json: {role}.{category} nivel inválido '{level}'")

# Reglas del usuario, comprobadas de forma explícita.
NO_CODE = {"product-owner", "ux-designer", "ui-designer", "content-writer", "communications-lead", "growth-analyst", "discovery-lead", "orchestrator"}
for role in NO_CODE & set(matrix):
    for category in ("product-code", "unit-tests"):
        if matrix[role][category] != "none":
            fail(f"regla de permisos: {role} no debe leer {category}")

for role in ("qa-lead", "qa-automation"):
    for category in ("product-code", "unit-tests"):
        if matrix[role][category] != "none":
            fail(f"regla de permisos: {role} no debe leer {category} (solo e2e)")
    if matrix[role]["e2e-tests"] == "none":
        fail(f"regla de permisos: {role} debe poder leer e2e-tests")

# La suite que verifica el sistema de agentes no es una superficie de trabajo de los roles.
SYSTEM_TESTS_WRITERS = {"org-architect", "structure-steward"}
SYSTEM_TESTS_READERS = {"orchestrator", "devops-engineer"}
for role, row in matrix.items():
    level = row["system-tests"]
    if role in SYSTEM_TESTS_WRITERS and level != "write":
        fail(f"regla de permisos: {role} debe poder escribir system-tests")
    elif role in SYSTEM_TESTS_READERS and level not in {"read", "write"}:
        fail(f"regla de permisos: {role} debe poder leer system-tests")
    elif role not in SYSTEM_TESTS_WRITERS | SYSTEM_TESTS_READERS and level != "none":
        fail(f"regla de permisos: {role} no debe tocar system-tests (es el sistema de agentes)")

for role in matrix:
    for category in ("spec", "brief", "decision"):
        if matrix[role][category] == "none":
            fail(f"regla de permisos: {role} debe poder leer {category} (terreno común)")

# 5b. La regla de preguntar tiene que estar en la política y en las cards.
policy = ROOT / "agents" / "policy" / "delivery.yaml"
policy_text = policy.read_text(encoding="utf-8")
for needle in ("when_unsure", "never_invent"):
    if needle not in policy_text:
        fail(f"policy/delivery.yaml: falta la sección '{needle}'")
for path in sorted(CARDS.glob("*.yaml")):
    if "when_unsure" not in path.read_text(encoding="utf-8"):
        fail(f"cards/{path.name}: no recuerda la regla de preguntar")

# 5b bis. Las firmas que exige la política tienen que ser roles reales, y el tablero tiene que
# poder representarlas: una firma condicional sin hueco es una firma que nadie ve.
closure = (load_yaml(policy) or {}).get("closure") or {}
for key in ("signatures_required", "conditional_signatures"):
    for line in closure.get(key) or []:
        text = str(line)
        if not any(role in text for role in role_ids):
            fail(f"policy/delivery.yaml: {key} cita una firma sin rol del registro: '{text}'")

backlog = json.loads((ROOT / "agents" / "templates" / "backlog.json").read_text(encoding="utf-8"))
task_format = backlog.get("_task_format") or {}
for key in ("signatures_required", "signatures"):
    if key not in task_format:
        fail(f"templates/backlog.json: _task_format debe declarar '{key}'")
for task in backlog.get("tasks") or []:
    required = task.get("signatures_required")
    signed = task.get("signatures")
    if not isinstance(required, list) or not isinstance(signed, dict):
        fail("templates/backlog.json: cada tarea declara signatures_required (lista) y signatures (mapa)")
        continue
    if set(signed) != set(required):
        fail("templates/backlog.json: signatures debe tener un hueco por rol de signatures_required")
    unknown = [role for role in required if role not in role_ids]
    if unknown:
        fail(f"templates/backlog.json: signatures_required cita roles que no existen: {unknown}")

feature_template = load_yaml(ROOT / "agents" / "templates" / "feature.yaml") or {}
if "signatures_required" not in feature_template:
    fail("templates/feature.yaml: falta 'signatures_required'")
if "categories" not in feature_template:
    fail("templates/feature.yaml: falta 'categories' (el packet las copia y se validan)")

packet_template = json.loads((ROOT / "agents" / "templates" / "packet.json").read_text(encoding="utf-8"))
if "categories" not in packet_template:
    fail("templates/packet.json: falta 'categories' (sin ellas no se puede comprobar el permiso)")
for tool in ("session.py", "board.py", "authz.py", "paths.py", "validate.py", "build-cards.py"):
    if not (ROOT / "agents" / "tools" / tool).is_file():
        fail(f"falta agents/tools/{tool}")
if not (ROOT / "agents" / "templates" / "memory.yaml").is_file():
    fail("falta agents/templates/memory.yaml: la rotación depende de la memoria del rol")

sessions_template = json.loads(
    (ROOT / "agents" / "templates" / "sessions.json").read_text(encoding="utf-8")
)
if sessions_template.get("$schema_version") != 1:
    fail("templates/sessions.json: '$schema_version' debe ser 1")

for required in (
    ROOT / "agents" / "policy" / "sessions.yaml",
    ROOT / "agents" / "policy" / "providers.yaml",
    ROOT / "agents" / "policy" / "measurement.yaml",
    ROOT / "agents" / "templates" / "sessions.json",
):
    if not required.exists():
        fail(f"falta {required.relative_to(ROOT)}")

# 5d. Perfiles de entorno: el sistema no puede depender de un proveedor concreto.
providers = load_yaml(ROOT / "agents" / "policy" / "providers.yaml") or {}
profiles = providers.get("profiles") or {}
default_profile = providers.get("default_profile")
if default_profile not in profiles:
    fail(f"providers.yaml: default_profile '{default_profile}' no existe en profiles")
for name, profile in profiles.items():
    if not isinstance(profile, dict):
        fail(f"providers.yaml: el perfil '{name}' no es un mapa")
        continue
    if profile.get("prompt_cache"):
        for key in ("read_factor", "write_factor", "ttl_minutes"):
            if key not in profile:
                fail(f"providers.yaml: el perfil '{name}' declara caché sin '{key}'")

sessions_policy = (ROOT / "agents" / "policy" / "sessions.yaml").read_text(encoding="utf-8")
if "providers.yaml" not in sessions_policy:
    fail("policy/sessions.yaml: no referencia la calibración de providers.yaml")
if "measurement.yaml" not in sessions_policy:
    fail("policy/sessions.yaml: no referencia el protocolo de measurement.yaml")

# 5d bis. El presupuesto se deriva de la ventana declarada; no hay umbrales fijos.
for key in ("context_window_tokens", "loaded_fraction", "max_tasks_ceiling", "max_turns_ceiling"):
    if key not in (providers.get("parameters") or {}):
        fail(f"providers.yaml: falta '{key}' en parameters")
if "limits" not in providers:
    fail("providers.yaml: falta la derivación de topes de la sección 'limits'")

# Los parámetros son números: un valor absurdo no rompe el YAML, rompe la decisión en silencio.
parameters = providers.get("parameters") or {}
for key in (
    "context_window_tokens",
    "bytes_per_turn",
    "bytes_per_token",
    "max_tasks_ceiling",
    "max_turns_ceiling",
    "max_age_hours",
    "default_turns_per_task",
):
    value = parameters.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        fail(f"providers.yaml: '{key}' debe ser un número mayor que cero (es {value!r})")
fraction = parameters.get("loaded_fraction")
if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not 0 < fraction <= 1:
    fail(f"providers.yaml: 'loaded_fraction' debe estar entre 0 y 1 (es {fraction!r})")
for name, profile in profiles.items():
    for key in ("context_window_tokens", "max_tasks_ceiling", "max_turns_ceiling", "loaded_fraction"):
        if key not in profile:
            continue
        value = profile[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            fail(f"providers.yaml: '{name}.{key}' debe ser un número mayor que cero (es {value!r})")
sessions_doc = load_yaml(ROOT / "agents" / "policy" / "sessions.yaml") or {}
if "window" not in (sessions_doc.get("budget") or {}):
    fail("policy/sessions.yaml: el budget no se deriva de la ventana declarada")
memory_policy = sessions_doc.get("memory") or {}
for key in ("path", "keep", "drop"):
    if key not in memory_policy:
        fail(f"policy/sessions.yaml#memory: falta '{key}'")
limit = memory_policy.get("max_bytes")
if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
    fail("policy/sessions.yaml#memory.max_bytes debe ser un entero positivo")

# 5e. El protocolo de medición no puede depender de un IDE ni de un proveedor.
measurement = load_yaml(ROOT / "agents" / "policy" / "measurement.yaml") or {}
for key in ("sources", "journal", "rules"):
    if key not in measurement:
        fail(f"measurement.yaml: falta '{key}'")
for event in ("turn", "read", "write", "note"):
    if event not in (measurement.get("journal", {}).get("events") or {}):
        fail(f"measurement.yaml: el journal no declara el evento '{event}'")

# 5c. Las cards se generan desde el rol: si alguien las edita a mano, se detecta.
cards_check = subprocess.run(
    [sys.executable, str(ROOT / "agents" / "tools" / "build-cards.py"), "--check"],
    capture_output=True,
    text=True,
)
if cards_check.returncode != 0:
    fail("cards desactualizadas respecto a su rol: " + cards_check.stdout.strip().replace("\n", " "))

# 6. Enlaces relativos en markdown.
LINK = re.compile(r"\]\(([^)#]+?)\)")
for path in ROOT.rglob("*.md"):
    if ".git" in path.parts or ".orchestrator" in path.parts:
        continue
    text = path.read_text(encoding="utf-8")
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (path.parent / target).resolve().exists():
            fail(f"{path.relative_to(ROOT)}: enlace roto → {target}")

if errors:
    print(f"{len(errors)} error(es):")
    for item in errors:
        print(f"  - {item}")
    sys.exit(1)

print(f"OK · {len(role_ids)} roles, {len(card_ids)} cards, {len(categories)} categorías de permisos")
print(f"     capas: {len(registry['layers'])} · flujos: {len(registry['flows'])}")
