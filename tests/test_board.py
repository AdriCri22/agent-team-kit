"""Tests del validador de tablero (`agents/tools/board.py`).

Cada caso escribe un tablero mínimo en un directorio temporal, con sus especificaciones y packets
reales: el lint no se simula, se ejecuta como lo ejecutará el orquestador.
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
BOARD = ROOT / "agents" / "tools" / "board.py"
SIGNATURES = ["tech-lead-backend", "qa-lead", "product-owner"]


class BoardLint(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        (self.tmp / "specs").mkdir()
        (self.tmp / "packets").mkdir()
        self.spec = self.tmp / "specs" / "f.spec.yaml"
        self.spec.write_text(
            yaml.safe_dump({"status": "approved", "approved_by": ["product-owner"]}),
            encoding="utf-8",
        )

    def task(self, **overrides) -> dict:
        task = {
            "task": "T-01",
            "title": "x",
            "spec": "specs/f.spec.yaml",
            "owner": "backend-engineer",
            "status": "todo",
            "wave": 1,
            "deps": [],
            "branch": None,
            "pr": None,
            "packet": "packets/T-01.json",
            "signatures_required": list(SIGNATURES),
            "signatures": {role: None for role in SIGNATURES},
            "notes": "",
        }
        task.update(overrides)
        return task

    def packet(self, task_id: str, write: list[str], **extra) -> None:
        data = {
            "role": "backend-engineer",
            "spec": "specs/f.spec.yaml",
            "categories": {"spec": "read", "product-code": "write"},
            "write": write,
            **extra,
        }
        (self.tmp / "packets" / f"{task_id}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def board(self, *tasks: dict) -> pathlib.Path:
        path = self.tmp / "backlog.json"
        path.write_text(json.dumps({"tasks": list(tasks)}), encoding="utf-8")
        return path

    def lint(self, board: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BOARD), "lint", "--board", str(board)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    # --- casos ------------------------------------------------------------

    def test_valid_board_passes(self) -> None:
        self.packet("T-01", ["src/a.ts"])
        result = self.lint(self.board(self.task()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("tablero coherente", result.stdout)

    def test_done_without_signatures_fails(self) -> None:
        self.packet("T-01", ["src/a.ts"])
        task = self.task(status="done", branch="agent/x", pr="https://pr/1")
        task["signatures"]["qa-lead"] = "revisado"
        result = self.lint(self.board(task))
        self.assertEqual(result.returncode, 1)
        self.assertIn("firmas pendientes", result.stdout)
        self.assertIn("tech-lead-backend", result.stdout)

    def test_in_progress_with_open_dependency_fails(self) -> None:
        self.packet("T-02", ["src/b.ts"])
        board = self.board(
            self.task(task="T-01", status="todo"),
            self.task(
                task="T-02",
                status="in_progress",
                branch="agent/y",
                deps=["T-01"],
                packet="packets/T-02.json",
            ),
        )
        result = self.lint(board)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dependencia 'T-01' abierta", result.stdout)

    def test_duplicate_ids_fail(self) -> None:
        result = self.lint(self.board(self.task(), self.task()))
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicada", result.stdout)

    def test_cycle_fails(self) -> None:
        self.packet("T-01", ["src/a.ts"])
        self.packet("T-02", ["src/b.ts"])
        board = self.board(
            self.task(task="T-01", status="in_progress", branch="agent/a", deps=["T-02"]),
            self.task(
                task="T-02",
                status="in_progress",
                branch="agent/b",
                deps=["T-01"],
                packet="packets/T-02.json",
            ),
        )
        result = self.lint(board)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ciclo", result.stdout)

    def test_same_wave_overlap_fails(self) -> None:
        self.packet("T-01", ["src/app.ts"])
        self.packet("T-02", ["src/*.ts"])
        board = self.board(
            self.task(task="T-01", status="in_progress", branch="agent/a"),
            self.task(
                task="T-02",
                status="in_progress",
                branch="agent/b",
                packet="packets/T-02.json",
            ),
        )
        result = self.lint(board)
        self.assertEqual(result.returncode, 1)
        self.assertIn("misma superficie", result.stdout)

    def test_overlapping_globs_are_flagged(self) -> None:
        self.packet("T-01", ["src/*"])
        self.packet("T-02", ["src/*.ts"])
        board = self.board(
            self.task(task="T-01", status="in_progress", branch="agent/a"),
            self.task(
                task="T-02",
                status="in_progress",
                branch="agent/b",
                packet="packets/T-02.json",
            ),
        )
        result = self.lint(board)
        self.assertEqual(result.returncode, 1)
        self.assertIn("misma superficie", result.stdout)

    def test_different_globs_do_not_overlap(self) -> None:
        self.packet("T-01", ["src/*.py"])
        self.packet("T-02", ["src/*.ts"])
        board = self.board(
            self.task(task="T-01", status="in_progress", branch="agent/a"),
            self.task(
                task="T-02",
                status="in_progress",
                branch="agent/b",
                packet="packets/T-02.json",
            ),
        )
        result = self.lint(board)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_done_without_pr_fails(self) -> None:
        self.packet("T-01", ["src/a.ts"])
        result = self.lint(self.board(self.task(status="done", branch="agent/x")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("sin PR", result.stdout)

    def test_unapproved_spec_fails(self) -> None:
        self.spec.write_text(yaml.safe_dump({"status": "draft"}), encoding="utf-8")
        self.packet("T-01", ["src/a.ts"])
        result = self.lint(self.board(self.task(status="in_progress", branch="agent/x")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no está aprobada", result.stdout)

    def test_unknown_owner_fails(self) -> None:
        result = self.lint(self.board(self.task(owner="arquitecto")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no es un rol", result.stdout)

    def test_missing_board_is_usage_error(self) -> None:
        result = self.lint(self.tmp / "no-existe.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no encuentro el tablero", result.stderr)

    def test_packet_outside_the_matrix_fails(self) -> None:
        self.packet(
            "T-01",
            ["src/a.ts"],
            role="qa-lead",
            categories={"spec": "read", "product-code": "read"},
        )
        result = self.lint(
            self.board(self.task(owner="qa-lead", status="in_progress", branch="agent/x"))
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("product-code", result.stdout)

    def test_packet_role_must_match_the_owner(self) -> None:
        self.packet("T-01", ["src/a.ts"], role="qa-lead")
        result = self.lint(self.board(self.task(status="in_progress", branch="agent/x")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no del dueño", result.stdout)

    def test_packet_spec_must_match_the_task(self) -> None:
        self.packet("T-01", ["src/a.ts"], spec="specs/otra.spec.yaml")
        result = self.lint(self.board(self.task(status="in_progress", branch="agent/x")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("otra especificación", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
