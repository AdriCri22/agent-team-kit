"""Tests de la CLI de orquestación (`agents/tools/orch.py`).

Se ejecuta el script real contra un producto temporal: nada se simula.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ORCH = ROOT / "agents" / "tools" / "orch.py"

import importlib.util

_SPEC = importlib.util.spec_from_file_location("orch_tool", ORCH)
orch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(orch)


class Orch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.product = pathlib.Path(self._tmp.name)
        (self.product / "docs" / "planning" / "tasks").mkdir(parents=True)
        (self.product / "packets").mkdir()
        (self.product / "tickets").mkdir()
        (self.product / "specs").mkdir()
        (self.product / "specs" / "f.spec.yaml").write_text(
            yaml.safe_dump({"status": "approved", "approved_by": ["product-owner"]}),
            encoding="utf-8",
        )
        (self.product / "board.json").write_text(
            json.dumps({"_doc": "x", "updated_at": "2026-01-01", "progress": "", "tasks": []}),
            encoding="utf-8",
        )
        self.ficha = self.product / "docs" / "planning" / "tasks" / "T-01.yaml"
        self.ficha.write_text(
            yaml.safe_dump(
                {
                    "task": "T-01",
                    "title": "Tarea de prueba",
                    "spec": "specs/f.spec.yaml",
                    "owner": "backend-engineer",
                    "acceptance_refs": ["CF-BASE-1"],
                    "surface": {
                        "write": ["db/migrations/**"],
                        "allowed": ["db/**"],
                        "forbidden": [".env*"],
                    },
                    "categories": {"spec": "read", "data-model": "write"},
                    "gates": [{"name": "noop", "command": "python3 -c \"print('ok')\""}],
                    "depends_on": [],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def run_orch(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ORCH), "--root", str(self.product), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.product),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def board(self) -> dict:
        return json.loads((self.product / "board.json").read_text(encoding="utf-8"))

    def add(self) -> None:
        result = self.run_orch(
            "board", "add", "T-01", "--title", "Tarea de prueba",
            "--spec", "specs/f.spec.yaml", "--owner", "backend-engineer",
            "--wave", "1", "--signatures", "tech-lead-backend,qa-lead,product-owner",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # --- ready ------------------------------------------------------------

    def _ready_board(self, tasks: list[dict]) -> None:
        board = self.board()
        board["tasks"] = tasks
        (self.product / "board.json").write_text(json.dumps(board), encoding="utf-8")
        for task in tasks:
            (self.product / "packets" / f"{task['task']}.json").write_text("{}", encoding="utf-8")
            ficha = self.product / "docs" / "planning" / "tasks" / f"{task['task']}.yaml"
            if not ficha.exists():
                ficha.write_text(yaml.safe_dump({"task": task["task"]}), encoding="utf-8")

    def _task(self, task_id: str, owner: str, status: str = "todo", deps: list[str] | None = None) -> dict:
        return {"task": task_id, "title": task_id, "spec": "specs/f.spec.yaml", "owner": owner, "status": status,
                "wave": 1, "deps": deps or [], "packet": f"packets/{task_id}.json"}

    def test_ready_lists_admissible_tasks_without_lots(self) -> None:
        self._ready_board([self._task("A-01", "backend-engineer"), self._task("A-02", "backend-engineer", deps=["A-01"])])
        result = self.run_orch("ready", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["task"]: row for row in json.loads(result.stdout)["tasks"]}
        self.assertTrue(rows["A-01"]["ready"])
        self.assertFalse(rows["A-02"]["ready"])
        self.assertIn("espera a A-01 (todo)", rows["A-02"]["blockers"])

    def test_ready_treats_retired_dependencies_as_closed_and_flags_unapproved_specs(self) -> None:
        self._ready_board([self._task("B-01", "backend-engineer", deps=["RETIRADA"])])
        rows = json.loads(self.run_orch("ready", "--json").stdout)["tasks"]
        self.assertTrue(rows[0]["ready"])
        (self.product / "specs" / "f.spec.yaml").write_text(yaml.safe_dump({"status": "draft"}), encoding="utf-8")
        rows = json.loads(self.run_orch("ready", "--json").stdout)["tasks"]
        self.assertIn("specs/f.spec.yaml sin aprobar", rows[0]["blockers"])

    def test_ready_reports_next_lot_per_lane_and_pairs_do_not_block(self) -> None:
        self._ready_board([
            self._task("P-01", "backend-engineer"), self._task("P-02", "backend-engineer", deps=["P-01"]),
            self._task("F-01", "frontend-engineer", status="in_progress"),
            self._task("Q-01", "qa-automation", deps=["F-01"]),
            self._task("P-03", "backend-engineer", deps=["F-01"]),
        ])
        lots = {"lanes": {
            "backend": [{"id": "L-B1", "title": "b", "tasks": ["P-01", "P-02"]}, {"id": "L-B2", "title": "b2", "tasks": ["P-03"]}],
            "frontend": [{"id": "L-F1", "title": "f", "tasks": ["F-01"], "pair_with": "L-Q1"}],
            "qa": [{"id": "L-Q1", "title": "q", "tasks": ["Q-01"]}],
        }}
        (self.product / "docs" / "planning" / "lots.yaml").write_text(yaml.safe_dump(lots), encoding="utf-8")
        lanes = json.loads(self.run_orch("ready", "--json").stdout)["lanes"]
        self.assertEqual((lanes["backend"]["lot"], lanes["backend"]["state"]), ("L-B1", "listo"))
        self.assertEqual(lanes["frontend"]["state"], "en curso")
        self.assertEqual(lanes["qa"]["state"], "listo")
        self.assertEqual(lanes["qa"]["pair_with"], ["L-F1"])

    # --- tablero ----------------------------------------------------------

    def test_add_keeps_the_board_format(self) -> None:
        self.add()
        raw = (self.product / "board.json").read_bytes()
        self.assertIn(b"\n", raw)
        self.assertEqual(len(self.board()["tasks"]), 1)
        self.assertEqual(self.board()["tasks"][0]["status"], "todo")

    def test_add_rejects_duplicates(self) -> None:
        self.add()
        result = self.run_orch(
            "board", "add", "T-01", "--title", "otra", "--spec", "specs/f.spec.yaml",
            "--owner", "backend-engineer", "--wave", "1",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ya existe", result.stderr)

    def test_author_cannot_sign(self) -> None:
        self.add()
        result = self.run_orch(
            "board", "sign", "T-01", "--role", "backend-engineer", "--text", "vale"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("autor", result.stderr)

    def test_sign_then_done_and_remove(self) -> None:
        self.add()
        for role in ("tech-lead-backend", "qa-lead", "product-owner"):
            result = self.run_orch("board", "sign", "T-01", "--role", role, "--text", "ok")
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_orch(
            "board", "set", "T-01", "--status", "in_progress", "--branch", "feat/t-01"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_orch("board", "done", "T-01", "--pr", "https://pr/1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.board()["tasks"][0]["status"], "done")
        result = self.run_orch("board", "remove", "T-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.board()["tasks"], [])
        self.assertIn("quedan 0 tareas", self.board()["progress"])

    def test_board_progress_appends(self) -> None:
        result = self.run_orch("board", "progress", "--text", "Ola 34 abierta")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ola 34 abierta", self.board()["progress"])

    def test_done_with_pending_signatures_fails(self) -> None:
        self.add()
        result = self.run_orch("board", "done", "T-01", "--pr", "https://pr/1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("firmas pendientes", result.stderr)

    # --- packets y tickets ------------------------------------------------

    def test_packet_new_reads_the_ficha(self) -> None:
        result = self.run_orch("packet", "new", "T-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        packet = json.loads((self.product / "packets" / "T-01.json").read_text(encoding="utf-8"))
        self.assertEqual(packet["role"], "backend-engineer")
        self.assertEqual(packet["write"], ["db/migrations/**"])
        self.assertEqual(packet["acceptance_refs"], ["CF-BASE-1"])
        self.assertIn("docs/planning/tasks/T-01.yaml", packet["read"]["must"])

    def test_ticket_new_list_and_close(self) -> None:
        result = self.run_orch(
            "ticket", "new", "UI-100", "--title", "Algo se rompe", "--severity", "S3",
            "--spec", "specs/f.spec.yaml", "--what", "Pasa algo",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        listed = self.run_orch("tickets", "list", "--open")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("UI-100", listed.stdout)
        closed = self.run_orch(
            "ticket", "close", "UI-100", "--note", "resuelto", "--pr", "https://pr/2"
        )
        self.assertEqual(closed.returncode, 0, closed.stderr)
        listed = self.run_orch("tickets", "list", "--open")
        self.assertNotIn("UI-100", listed.stdout)

    # --- gates ------------------------------------------------------------

    def test_gates_dry_run_lists_commands(self) -> None:
        self.run_orch("packet", "new", "T-01")
        result = self.run_orch("gates", "T-01", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("noop", result.stdout)

    def test_gates_runs_the_command(self) -> None:
        self.run_orch("packet", "new", "T-01")
        result = self.run_orch("gates", "T-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1/1 gates en verde", result.stdout)

    def test_remove_links_never_deletes_a_real_directory(self) -> None:
        real = self.product / "node_modules"
        real.mkdir()
        (real / "paquete.txt").write_text("x", encoding="utf-8")
        removed = orch.remove_links(self.product)
        self.assertEqual(removed, 0)
        self.assertTrue((real / "paquete.txt").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
