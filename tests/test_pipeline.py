"""Flujo completo sobre un producto de usar y tirar.

Copia `agents/` dentro de un producto temporal y recorre la secuencia que hacen orquestador y
worker:

    tablero → packet → check → open → decide → record → close → verify → board lint

Lo que se prueba son las costuras: que la especificación del tablero y la del packet sean la
misma, que el estado viva en el producto y no en el directorio desde el que se ejecuta, que el
cierre valide el tablero y que el diff verificado sea el de la rama del PR. Las herramientas se
ejecutan copiadas, así que `PROJECT_ROOT` es el producto temporal: nada toca el repositorio real.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"

ROLE = "backend-engineer"
SIGNATURES = ["tech-lead-backend", "qa-lead", "product-owner"]


class Pipeline(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.product = pathlib.Path(self._tmp.name)
        shutil.copytree(AGENTS, self.product / "agents")
        for folder in ("specs", "packets", "memory"):
            (self.product / folder).mkdir()

        (self.product / "specs" / "feat.spec.yaml").write_text(
            yaml.safe_dump({"status": "approved", "approved_by": ["product-owner"]}),
            encoding="utf-8",
        )
        self.packet = self.product / "packets" / "T-01.json"
        self.write_packet({"spec": "read", "product-code": "write", "unit-tests": "write"})
        self.board = self.product / "backlog.json"
        self.write_board("in_progress")

    # --- utilidades -------------------------------------------------------

    def write_packet(self, categories: dict) -> None:
        self.packet.write_text(
            json.dumps(
                {
                    "task": "T-01",
                    "title": "primera",
                    "role": ROLE,
                    "spec": "specs/feat.spec.yaml",
                    "categories": categories,
                    "write": ["src/a.py"],
                    "gates": [],
                    "independent": False,
                }
            ),
            encoding="utf-8",
        )

    def write_board(self, status: str, **overrides) -> None:
        task = {
            "task": "T-01",
            "title": "primera",
            "spec": "specs/feat.spec.yaml",
            "owner": ROLE,
            "status": status,
            "wave": 1,
            "deps": [],
            "branch": "agent/backend/T-01",
            "pr": None,
            "packet": "packets/T-01.json",
            "signatures_required": list(SIGNATURES),
            "signatures": {role: None for role in SIGNATURES},
            "notes": "",
        }
        task.update(overrides)
        self.board.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")

    def run_tool(self, tool: str, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.product / "agents" / "tools" / tool), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.product),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def session(self, *arguments: str) -> subprocess.CompletedProcess:
        return self.run_tool("session.py", *arguments)

    def git(self, *arguments: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", *arguments],
            cwd=self.product,
            check=True,
            capture_output=True,
        )

    def branch_with_the_change(self) -> None:
        (self.product / "src").mkdir(exist_ok=True)
        (self.product / "src" / "a.py").write_text("antes\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("checkout", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("checkout", "-q", "-b", "agent/backend/T-01")
        (self.product / "src" / "a.py").write_text("después\n", encoding="utf-8")
        self.git("commit", "-qam", "cambio")

    # --- casos ------------------------------------------------------------

    def test_full_flow_from_board_to_verified_diff(self) -> None:
        board = self.run_tool("board.py", "lint", "--board", "backlog.json")
        self.assertEqual(board.returncode, 0, board.stdout + board.stderr)

        check = self.session("check", "--packet", "packets/T-01.json")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

        opened = self.session(
            "open",
            "--role",
            ROLE,
            "--model",
            "modelo",
            "--domain",
            "dominio",
            "--spec",
            "specs/feat.spec.yaml",
            "--window",
            "200000",
        )
        self.assertEqual(opened.returncode, 0, opened.stdout + opened.stderr)
        self.assertTrue(
            (self.product / "sessions.json").is_file(),
            "el estado de sesiones no vive en el producto",
        )

        decided = self.session(
            "decide",
            "--role",
            ROLE,
            "--spec",
            "specs/feat.spec.yaml",
            "--model",
            "modelo",
            "--domain",
            "dominio",
            "--packet",
            "packets/T-01.json",
        )
        self.assertEqual(decided.returncode, 0, decided.stdout + decided.stderr)
        self.assertIn("REUSE", decided.stdout)

        self.assertEqual(
            self.session("record", "--role", ROLE, "--event", "turn").returncode, 0
        )
        (self.product / "memory" / f"{ROLE}.yaml").write_text(
            f"role: {ROLE}\n", encoding="utf-8"
        )

        closed = self.session(
            "close",
            "--role",
            ROLE,
            "--task",
            "T-01",
            "--memory",
            f"memory/{ROLE}.yaml",
            "--board",
            "backlog.json",
        )
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)

        self.branch_with_the_change()
        verified = self.session(
            "verify", "--packet", "packets/T-01.json", "--branch", "agent/backend/T-01", "--base", "main"
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

        self.write_board(
            "done",
            pr="https://pr/1",
            signatures={role: "firmado" for role in SIGNATURES},
        )
        board = self.run_tool("board.py", "lint", "--board", "backlog.json")
        self.assertEqual(board.returncode, 0, board.stdout + board.stderr)

    def test_the_chain_stops_when_the_packet_expands_permissions(self) -> None:
        self.write_packet({"spec": "read", "infra": "write"})

        check = self.session("check", "--packet", "packets/T-01.json")
        self.assertEqual(check.returncode, 1, check.stdout)
        self.assertIn("infra", check.stderr)

        board = self.run_tool("board.py", "lint", "--board", "backlog.json")
        self.assertEqual(board.returncode, 1, board.stdout)
        self.assertIn("infra", board.stdout)

    def test_the_chain_stops_when_the_close_contradicts_the_board(self) -> None:
        self.assertEqual(
            self.session(
                "open",
                "--role",
                ROLE,
                "--model",
                "modelo",
                "--domain",
                "dominio",
                "--spec",
                "specs/feat.spec.yaml",
            ).returncode,
            0,
        )
        self.write_board("in_progress", owner="frontend-engineer")

        closed = self.session(
            "close", "--role", ROLE, "--task", "T-01", "--board", "backlog.json"
        )
        self.assertEqual(closed.returncode, 1, closed.stdout)
        self.assertIn("es de 'frontend-engineer'", closed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
