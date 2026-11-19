"""Tests del CLI de sesiones.

Verifican el contrato que usan los agentes —códigos 0/2/10, topes derivados, huella, economía—
sin tocar el registro real: cada caso trabaja en un directorio temporal con su propio registro,
su journal y su especificación.

No son herramientas de rol: se ejecutan antes de abrir el PR, no durante una tarea.

    python3 -m unittest discover -s tests -p "test_*.py"
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SESSION = ROOT / "agents" / "tools" / "session.py"
ROLE = "qa-lead"
MODEL = "modelo-de-prueba"
DOMAIN = "dominio"

CAPS = re.compile(r"tareas (\d+)/(\d+) · turnos (\d+)/(\d+)")
COST = re.compile(r"reutilizar[^0-9]*([\d,]+)\s+B")


class SessionCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = pathlib.Path(self.tmpdir.name)
        self.registry = self.tmp / "sessions.json"
        self.spec = self.tmp / "feature.spec.yaml"
        self.spec.write_text("id: prueba\ntitle: prueba\n", encoding="utf-8")

        policy = yaml.safe_load((ROOT / "agents/policy/providers.yaml").read_text(encoding="utf-8")) or {}
        self.parameters = policy.get("parameters") or {}
        self.per_turn = int(self.parameters.get("bytes_per_turn", 1500))

    # --- utilidades -------------------------------------------------------

    def run_cli(self, *arguments: str, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SESSION), "--registry", str(self.registry), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd or ROOT),
            # Sin esto el hijo escribe con la codificación de la consola y los acentos se pierden.
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def open_session(self, window: int = 1_000_000, role: str = ROLE, spec: pathlib.Path | None = None):
        return self.run_cli(
            "open",
            "--role",
            role,
            "--model",
            MODEL,
            "--domain",
            DOMAIN,
            "--spec",
            str(spec or self.spec),
            "--window",
            str(window),
        )

    def close_task(self, task: str, turns: int = 1):
        return self.run_cli("close", "--role", ROLE, "--task", task, "--turns", str(turns))

    def close_task_without_turns(self, task: str):
        return self.run_cli("close", "--role", ROLE, "--task", task)

    def record(self, event: str = "turn") -> None:
        self.assertEqual(self.run_cli("record", "--role", ROLE, "--event", event).returncode, 0)

    def decide(self, *extra: str, model: str = MODEL, domain: str = DOMAIN):
        return self.run_cli(
            "decide",
            "--role",
            ROLE,
            "--spec",
            str(self.spec),
            "--model",
            model,
            "--domain",
            domain,
            *extra,
        )

    def write_packet(self, write: list[str], **extra) -> pathlib.Path:
        packet = {
            "task": "T-01",
            "role": ROLE,
            "spec": str(self.spec),
            "categories": {"spec": "read", "e2e-tests": "read"},
            "write": write,
            **extra,
        }
        path = self.tmp / f"packet-{len(list(self.tmp.glob('packet-*')))}.json"
        path.write_text(json.dumps(packet), encoding="utf-8")
        return path

    def write_board(self, **overrides) -> pathlib.Path:
        task = {
            "task": "T-01",
            "title": "x",
            "spec": str(self.spec),
            "owner": ROLE,
            "status": "in_progress",
            "wave": 1,
            "deps": [],
            "branch": "agent/qa-lead/T-01",
            "pr": None,
            "packet": None,
            "signatures_required": ["tech-lead-backend", "qa-lead", "product-owner"],
            "signatures": {"tech-lead-backend": None, "qa-lead": None, "product-owner": None},
            "notes": "",
        }
        task.update(overrides)
        path = self.tmp / "backlog.json"
        path.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
        return path

    def caps(self) -> tuple[int, int, int, int]:
        """(tareas usadas, tareas máximas, turnos usados, turnos máximos) según `state`."""
        output = self.run_cli("state").stdout
        match = CAPS.search(output)
        self.assertIsNotNone(match, f"state no imprime topes:\n{output}")
        return tuple(int(group) for group in match.groups())

    def entry(self) -> dict:
        return json.loads(self.registry.read_text(encoding="utf-8"))["sessions"][0]

    def base_bytes(self) -> int:
        # Mismos cuatro documentos que `context_bytes` cuenta como base.
        paths = [
            ROOT / "agents/cards" / f"{ROLE}.yaml",
            ROOT / "agents/roles" / f"{ROLE}.yaml",
            self.spec,
            ROOT / "agents/policy/delivery.yaml",
        ]
        return sum(path.stat().st_size for path in paths)

    # --- presupuesto ------------------------------------------------------

    def test_budget_matches_documented_formula(self) -> None:
        fraction = float(self.parameters["loaded_fraction"])
        per_token = int(self.parameters["bytes_per_token"])
        turns_ceiling = int(self.parameters["max_turns_ceiling"])
        tasks_ceiling = int(self.parameters["max_tasks_ceiling"])
        per_task = int(self.parameters["default_turns_per_task"])
        base = self.base_bytes()

        for window in (32_000, 200_000, 1_000_000):
            self.assertEqual(self.open_session(window=window).returncode, 0)
            _, tasks_max, _, turns_max = self.caps()

            loaded = int(window * fraction * per_token)
            expected_turns = max(1, min(turns_ceiling, max(0, loaded - base) // self.per_turn))
            expected_tasks = max(1, min(tasks_ceiling, expected_turns // per_task))

            self.assertEqual(
                (turns_max, tasks_max),
                (expected_turns, expected_tasks),
                f"ventana {window}: la herramienta no aplica la fórmula documentada",
            )

    def test_window_never_reduces_limits(self) -> None:
        limits = []
        for window in (32_000, 200_000, 1_000_000):
            self.assertEqual(self.open_session(window=window).returncode, 0)
            _, tasks_max, _, turns_max = self.caps()
            limits.append((turns_max, tasks_max))

        self.assertEqual(limits, sorted(limits), f"la ventana reduce algún tope: {limits}")
        self.assertGreater(limits[-1][0], limits[0][0], "1 M no permite más turnos que 32 k")

    def test_packet_bytes_over_budget_rotates(self) -> None:
        self.assertEqual(self.open_session(window=32_000).returncode, 0)
        result = self.decide("--follow-up", "--packet-bytes", "1000000")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("contexto cargado", result.stdout)

    def test_age_rotates(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        data["sessions"][0]["opened_at"] = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.registry.write_text(json.dumps(data), encoding="utf-8")

        result = self.decide("--follow-up")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("la sesión tiene", result.stdout)

    # --- decisión ---------------------------------------------------------

    def test_decide_without_intent_reuses(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.decide()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("REUSE", result.stdout)
        self.assertIn("sin intención declarada", result.stdout)
        # La huella coincide por definición: pedir «secciones cambiadas» sería pedir algo imposible.
        self.assertNotIn("secciones de la especificación que cambiaron", result.stdout)
        self.assertIn("su huella no ha cambiado", result.stdout)

    def test_follow_up_reuses(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.decide("--follow-up")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("REUSE", result.stdout)

    def test_continuation_of_unknown_task_rotates(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.assertEqual(self.close_task("T-01").returncode, 0)
        result = self.decide("--continuation", "T-99")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("no la ha trabajado", result.stdout)

    def test_spec_change_rotates(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.spec.write_text("id: prueba\ntitle: prueba cambiada\n", encoding="utf-8")
        result = self.decide("--follow-up")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("documentos base", result.stdout)

    def test_model_change_rotates(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.decide(model="otro-modelo")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("cambia el modelo", result.stdout)

    def test_domain_change_rotates(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.decide(domain="otro-dominio")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("cambia el dominio", result.stdout)

    def test_independent_review_rotates(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.decide("--independent")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("revisión independiente", result.stdout)

    def test_packet_declaring_independent_review_rotates(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        packet = self.write_packet(["a.txt"], independent=True)
        result = self.decide("--packet", str(packet))
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("revisión independiente", result.stdout)

    def test_packet_from_another_role_is_usage_error(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        packet = self.write_packet(["a.txt"], role="qa-automation")
        result = self.decide("--packet", str(packet))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("el packet es del rol", result.stderr)

    def test_reuse_is_journaled(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.assertEqual(self.decide().returncode, 0)

        events = [
            json.loads(line)
            for line in (self.tmp / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        decisions = [event for event in events if event.get("type") == "decision"]
        self.assertEqual(len(decisions), 1, events)
        self.assertEqual(decisions[0]["outcome"], "REUSE")
        self.assertEqual(decisions[0]["role"], ROLE)

    # --- permisos del packet ----------------------------------------------

    def test_check_accepts_a_packet_within_the_matrix(self) -> None:
        packet = self.write_packet(["a.txt"])
        result = self.run_cli("check", "--packet", str(packet))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("packet válido", result.stdout)

    def test_check_rejects_a_category_the_role_cannot_read(self) -> None:
        packet = self.write_packet(["a.txt"], categories={"spec": "read", "product-code": "read"})
        result = self.run_cli("check", "--packet", str(packet))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("product-code", result.stderr)

    def test_check_rejects_an_unknown_category(self) -> None:
        packet = self.write_packet(["a.txt"], categories={"inventada": "read"})
        result = self.run_cli("check", "--packet", str(packet))
        self.assertEqual(result.returncode, 1)
        self.assertIn("categoría desconocida", result.stderr)

    def test_check_rejects_a_packet_without_categories(self) -> None:
        packet = self.write_packet(["a.txt"], categories={})
        result = self.run_cli("check", "--packet", str(packet))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no declara 'categories'", result.stderr)

    def test_verify_refuses_a_packet_that_expands_permissions(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        repo = self.make_repo()
        packet = self.write_packet(["a.txt"], categories={"product-code": "write"})
        result = self.run_cli("verify", "--packet", str(packet), "--base", "main", cwd=repo)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("fuera de la matriz", result.stderr)

    # --- memoria del rol --------------------------------------------------

    def test_close_warns_when_memory_is_missing(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.run_cli(
            "close", "--role", ROLE, "--task", "T-01", "--memory", str(self.tmp / "nada.yaml")
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("no encuentro la memoria", result.stdout)

    def test_close_warns_when_memory_is_stale_and_oversized(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        memory = self.tmp / "memoria.yaml"
        memory.write_text("x" * (9 * 1024), encoding="utf-8")
        old = time.time() - 3600
        os.utime(memory, (old, old))

        result = self.run_cli("close", "--role", ROLE, "--task", "T-01", "--memory", str(memory))
        self.assertIn("poda lo que ya no sea cierto", result.stdout)
        self.assertIn("no se ha actualizado", result.stdout)

    def test_close_accepts_a_fresh_memory(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        memory = self.tmp / "memoria.yaml"
        memory.write_text("role: qa-lead\n", encoding="utf-8")

        result = self.run_cli("close", "--role", ROLE, "--task", "T-01", "--memory", str(memory))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("aviso: la memoria", result.stdout)

    # --- tablero y sesión -------------------------------------------------

    def test_close_checks_the_task_against_the_board(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        board = self.write_board()
        result = self.run_cli("close", "--role", ROLE, "--task", "T-01", "--board", str(board))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.entry()["tasks_closed"], ["T-01"])

    def test_close_rejects_a_task_of_another_owner(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        board = self.write_board(owner="frontend-engineer")
        result = self.run_cli("close", "--role", ROLE, "--task", "T-01", "--board", str(board))
        self.assertEqual(result.returncode, 1)
        self.assertIn("es de 'frontend-engineer'", result.stderr)
        self.assertEqual(self.entry()["tasks_closed"], [], "se cerró con el tablero en contra")

    def test_close_rejects_a_task_that_is_not_in_progress(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        board = self.write_board(status="todo")
        result = self.run_cli("close", "--role", ROLE, "--task", "T-01", "--board", str(board))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no está en curso", result.stderr)

    def test_close_rejects_a_task_that_is_not_on_the_board(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        board = self.write_board(task="T-99")
        result = self.run_cli("close", "--role", ROLE, "--task", "T-01", "--board", str(board))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no está en el tablero", result.stderr)

    def test_close_reports_a_missing_board_as_usage_error(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.run_cli(
            "close", "--role", ROLE, "--task", "T-01", "--board", str(self.tmp / "nada.json")
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("no encuentro el tablero", result.stderr)

    def test_task_cap_rotates(self) -> None:
        self.assertEqual(self.open_session(window=20_000).returncode, 0)
        # Cerrar con un solo turno recalibra la media y sube el tope de tareas; el caso que hay que
        # probar es «una tarea cerrada y ningún turno declarado», que es el peor caso del tope.
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        data["sessions"][0]["tasks_closed"] = ["T-01"]
        data["sessions"][0]["turns"] = 0
        self.registry.write_text(json.dumps(data), encoding="utf-8")

        result = self.decide("--continuation", "T-01")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("tareas cerradas", result.stdout)

    def limits_only(self) -> tuple[int, int]:
        """(tareas máximas, turnos máximos), sin los contadores de uso."""
        _, tasks_max, _, turns_max = self.caps()
        return tasks_max, turns_max

    def test_closing_tasks_does_not_move_the_caps(self) -> None:
        self.assertEqual(self.open_session(window=20_000).returncode, 0)
        before = self.limits_only()

        self.assertIn(self.close_task("T-01", turns=1).returncode, (0, 10))
        self.assertEqual(self.limits_only(), before, "una tarea barata alargó la sesión")

        self.assertIn(self.close_task("T-02", turns=8).returncode, (0, 10))
        self.assertEqual(self.limits_only(), before, "una tarea cara acortó la sesión")

    def test_economics_follow_the_profile(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.assertEqual(self.close_task("T-01", turns=6).returncode, 0)

        plain = self.decide("--continuation", "T-01")
        cached = self.run_cli(
            "--profile",
            "prompt-cache",
            "decide",
            "--role",
            ROLE,
            "--spec",
            str(self.spec),
            "--model",
            MODEL,
            "--domain",
            DOMAIN,
            "--continuation",
            "T-01",
        )
        plain_cost = int(COST.search(plain.stdout).group(1).replace(",", ""))
        cached_cost = int(COST.search(cached.stdout).group(1).replace(",", ""))
        self.assertLess(cached_cost, plain_cost, "la caché de prefijo no abarata reutilizar")

    # --- errores de uso ---------------------------------------------------

    def test_missing_spec_is_usage_error(self) -> None:
        missing = self.tmp / "no-existe.yaml"
        self.assertEqual(self.open_session(spec=missing).returncode, 2)
        result = self.run_cli(
            "decide",
            "--role",
            ROLE,
            "--spec",
            str(missing),
            "--model",
            MODEL,
            "--domain",
            DOMAIN,
            "--follow-up",
        )
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("no encuentro la especificación", result.stderr)

    def test_decide_requires_model_and_domain(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        result = self.run_cli("decide", "--role", ROLE, "--spec", str(self.spec), "--follow-up")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("--model", result.stderr)
        self.assertIn("--domain", result.stderr)

    def test_state_shows_the_last_rotation_reason(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.assertEqual(self.decide(model="otro-modelo").returncode, 10)

        rotated = self.run_cli("state", "--role", ROLE)
        self.assertIn("última decisión: ROTATE · cambia el modelo", rotated.stdout)

        # Volver a reutilizar limpia el motivo: `state` no debe repetir una rotación que ya no rige.
        self.assertEqual(self.decide().returncode, 0)
        self.assertNotIn("ROTATE", self.run_cli("state", "--role", ROLE).stdout)

    def test_state_and_digest_ignore_the_working_directory(self) -> None:
        """El sistema puede vivir dentro de otro repo: documentos y estado no dependen del CWD."""
        elsewhere = self.tmp / "otro-sitio"
        elsewhere.mkdir()
        opened = self.run_cli(
            "open",
            "--role",
            ROLE,
            "--model",
            MODEL,
            "--domain",
            DOMAIN,
            "--spec",
            str(self.spec),
            "--window",
            "1000000",
            cwd=elsewhere,
        )
        self.assertEqual(opened.returncode, 0, opened.stdout)

        closed = self.run_cli("close", "--role", ROLE, "--task", "T-01", cwd=elsewhere)
        self.assertEqual(closed.returncode, 0, closed.stdout)

        entry = self.entry()
        self.assertGreater(entry["loaded_bytes"], 0, "los documentos base no se resolvieron")
        self.assertEqual(entry["spec"], str(self.spec), "la especificación no se ancló")

        decided = self.run_cli(
            "decide",
            "--role",
            ROLE,
            "--spec",
            str(self.spec),
            "--model",
            MODEL,
            "--domain",
            DOMAIN,
            cwd=elsewhere,
        )
        self.assertEqual(decided.returncode, 0, decided.stdout)

    def test_unknown_role_is_usage_error(self) -> None:
        self.assertEqual(self.open_session(role="backend-enginer").returncode, 2)
        result = self.run_cli("record", "--role", "backend-enginer", "--event", "turn")
        self.assertEqual(result.returncode, 2, result.stdout)

    # --- registro ---------------------------------------------------------

    def test_reopen_warns_and_keeps_one_session(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.assertEqual(self.close_task("T-01").returncode, 0)
        again = self.open_session()
        self.assertEqual(again.returncode, 0)
        self.assertIn("se descarta", again.stdout)

        sessions = json.loads(self.registry.read_text(encoding="utf-8"))["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["tasks_closed"], [])
        self.assertEqual(sessions[0]["turns"], 0)

    def test_record_refreshes_updated_at(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        before = self.entry()["updated_at"]
        time.sleep(1.05)
        self.assertEqual(self.run_cli("record", "--role", ROLE, "--event", "turn").returncode, 0)
        self.assertNotEqual(before, self.entry()["updated_at"], "record no refresca la sesión")

    def test_close_stores_one_formula(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.assertEqual(
            self.run_cli("record", "--role", ROLE, "--event", "read", "--path", "x", "--bytes", "900").returncode,
            0,
        )
        self.assertEqual(self.close_task("T-01", turns=5).returncode, 0)

        entry = self.entry()
        self.assertEqual(entry["loaded_bytes"], self.base_bytes() + 5 * self.per_turn)
        self.assertEqual(entry["bytes_read_reported"], 900)

    def test_close_accumulates_turns_across_tasks(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        for _ in range(3):
            self.record()
        self.assertEqual(self.close_task_without_turns("T-01").returncode, 0)
        self.assertEqual(self.entry()["turns"], 3)

        for _ in range(2):
            self.record()
        self.assertEqual(self.close_task_without_turns("T-02").returncode, 0)
        self.assertEqual(self.entry()["turns"], 5, "close sobrescribió los turnos acumulados")

    def test_journal_wins_over_declared_turns(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.record()
        self.record()
        self.assertEqual(self.close_task("T-01", turns=9).returncode, 0)

        entry = self.entry()
        self.assertEqual(entry["turns"], 2, "lo declarado pisó el journal")
        self.assertEqual(entry["turns_source"], "journal")

    def test_close_twice_does_not_double_count(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.record()
        self.record()
        self.assertEqual(self.close_task_without_turns("T-01").returncode, 0)
        self.assertEqual(self.close_task_without_turns("T-01").returncode, 0)

        entry = self.entry()
        self.assertEqual(entry["turns"], 2)
        self.assertEqual(entry["tasks_closed"], ["T-01"])

    def test_turn_ceiling_rotates_with_accumulated_turns(self) -> None:
        # Ventana de 80 k: caben 2 tareas y ~16 turnos con el coste por turno declarado. Una sola
        # tarea de 20 turnos agota los turnos sin agotar las tareas; con el contador sobrescrito
        # esto nunca se detectaba.
        self.assertEqual(self.open_session(window=80_000).returncode, 0)
        self.assertEqual(self.close_task("T-01", turns=20).returncode, 0)

        result = self.decide("--continuation", "T-01")
        self.assertEqual(result.returncode, 10, result.stdout)
        self.assertIn("turnos", result.stdout)

    def test_fallback_profile_matches_providers(self) -> None:
        """El valor de reserva del CLI y el declarado no pueden separarse.

        Se lee el literal con `ast` en vez de importar el módulo: la suite es de caja negra y no
        ejecuta la lógica de la herramienta.
        """
        tree = ast.parse(SESSION.read_text(encoding="utf-8"))
        fallback = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "FALLBACK_PROFILE"
                for target in node.targets
            ):
                fallback = ast.literal_eval(node.value)

        self.assertIsNotNone(fallback, "no se encontró FALLBACK_PROFILE en session.py")
        self.assertEqual(
            fallback["bytes_per_turn"],
            self.parameters["bytes_per_turn"],
            "FALLBACK_PROFILE y providers.yaml#parameters.bytes_per_turn divergen",
        )

    def test_registry_stays_valid_json(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        for _ in range(20):
            self.assertEqual(self.run_cli("record", "--role", ROLE, "--event", "turn").returncode, 0)
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(len(data["sessions"]), 1)

    def test_stats_shows_declared_and_journal_turns(self) -> None:
        self.assertEqual(self.open_session().returncode, 0)
        self.assertEqual(self.close_task("T-01", turns=5).returncode, 0)

        result = self.run_cli("stats")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("journal", result.stdout)
        self.assertIn("aviso: turnos declarados", result.stdout)

    def test_concurrent_records_do_not_lose_events(self) -> None:
        """Sin cerrojo, dos procesos que registran a la vez pisan el registro o el journal."""
        self.assertEqual(self.open_session().returncode, 0)
        workers = 6
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(SESSION),
                    "--registry",
                    str(self.registry),
                    "record",
                    "--role",
                    ROLE,
                    "--event",
                    "turn",
                ],
                cwd=str(ROOT),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            for _ in range(workers)
        ]
        outputs = [process.communicate(timeout=60) for process in processes]
        codes = [process.returncode for process in processes]
        self.assertEqual(codes, [0] * workers, [error for _, error in outputs])

        events = [
            json.loads(line)
            for line in (self.tmp / "sessions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(sum(1 for event in events if event.get("type") == "turn"), workers)
        self.assertEqual(len(json.loads(self.registry.read_text(encoding="utf-8"))["sessions"]), 1)

    def test_concurrent_closes_do_not_lose_tasks(self) -> None:
        """Leer-modificar-escribir sin cerrojo: el último cierre pisaba a los anteriores."""
        self.assertEqual(self.open_session().returncode, 0)
        tasks = ["T-01", "T-02", "T-03"]
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(SESSION),
                    "--registry",
                    str(self.registry),
                    "close",
                    "--role",
                    ROLE,
                    "--task",
                    task,
                    "--turns",
                    "2",
                ],
                cwd=str(ROOT),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            for task in tasks
        ]
        outputs = [process.communicate(timeout=60) for process in processes]
        self.assertEqual(
            [process.returncode for process in processes],
            [0] * len(tasks),
            [error for _, error in outputs],
        )

        entry = self.entry()
        self.assertEqual(sorted(entry["tasks_closed"]), sorted(tasks))
        self.assertEqual(entry["turns"], 2 * len(tasks))

    # --- verify -----------------------------------------------------------

    def git(self, repo: pathlib.Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", *arguments],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    def make_repo(self) -> pathlib.Path:
        """Repo con `main` y una rama `feat` que cambia a.txt y b.txt."""
        repo = self.tmp / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "checkout", "-q", "-b", "main")
        (repo / "a.txt").write_text("a", encoding="utf-8")
        (repo / "b.txt").write_text("b", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "base")
        self.git(repo, "checkout", "-q", "-b", "feat")
        (repo / "a.txt").write_text("a2", encoding="utf-8")
        (repo / "b.txt").write_text("b2", encoding="utf-8")
        self.git(repo, "commit", "-qam", "cambio")
        return repo

    def test_verify_flags_files_outside_write_set(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        repo = self.make_repo()

        packet = self.write_packet(["a.txt"])
        result = self.run_cli("verify", "--packet", str(packet), "--base", "main", cwd=repo)

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("1 fuera", result.stdout)
        self.assertIn("b.txt", result.stdout)

    def test_verify_refuses_empty_diff(self) -> None:
        # El falso verde: verificar desde la rama base imprime «0 ficheros, todo bien».
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        repo = self.make_repo()
        self.git(repo, "checkout", "-q", "main")

        packet = self.write_packet(["a.txt"])
        result = self.run_cli("verify", "--packet", str(packet), "--base", "main", cwd=repo)

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("no hay cambios", result.stderr)

    def test_verify_allow_empty_is_explicit(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        repo = self.make_repo()
        self.git(repo, "checkout", "-q", "main")

        packet = self.write_packet(["a.txt"])
        result = self.run_cli(
            "verify", "--packet", str(packet), "--base", "main", "--allow-empty", cwd=repo
        )

        self.assertEqual(result.returncode, 0, result.stdout)

    def test_verify_sees_uncommitted_and_untracked(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        repo = self.make_repo()
        (repo / "a.txt").write_text("a3", encoding="utf-8")  # cambio sin confirmar
        (repo / "c.txt").write_text("c", encoding="utf-8")  # fichero sin seguimiento

        packet = self.write_packet(["a.txt", "b.txt"])
        result = self.run_cli("verify", "--packet", str(packet), "--base", "main", cwd=repo)

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("1 fuera", result.stdout)
        self.assertIn("c.txt", result.stdout)
        self.assertIn("sin confirmar", result.stdout)

    def test_verify_accepts_the_pr_branch_from_another_checkout(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        repo = self.make_repo()
        self.git(repo, "checkout", "-q", "main")

        packet = self.write_packet(["a.txt"])
        result = self.run_cli(
            "verify", "--packet", str(packet), "--base", "main", "--branch", "feat", cwd=repo
        )

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("b.txt", result.stdout)

    def test_verify_star_does_not_cross_directories(self) -> None:
        """`*` se queda en un nivel y `**` cruza: un write set no se amplía sin decirlo."""
        if shutil.which("git") is None:
            self.skipTest("git no disponible")
        repo = self.tmp / "repo-estrecho"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "checkout", "-q", "-b", "main")
        (repo / "src").mkdir()
        (repo / "src" / "uno.ts").write_text("1", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "base")
        self.git(repo, "checkout", "-q", "-b", "feat")
        (repo / "src" / "uno.ts").write_text("1b", encoding="utf-8")
        (repo / "src" / "dos").mkdir()
        (repo / "src" / "dos" / "tres.ts").write_text("3", encoding="utf-8")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "cambio")

        narrow = self.write_packet(["src/*.ts"])
        result = self.run_cli("verify", "--packet", str(narrow), "--base", "main", cwd=repo)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("src/dos/tres.ts", result.stdout)

        wide = self.write_packet(["src/**"])
        result = self.run_cli("verify", "--packet", str(wide), "--base", "main", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
