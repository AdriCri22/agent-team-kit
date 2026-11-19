"""Tests del validador del sistema (`agents/tools/validate.py`).

La forma de probarlo es copiar `agents/` a un temporal, romper una regla y exigir el fallo: el
validador se ejecuta sobre el árbol copiado y nunca toca el real.

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"


class Validate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        shutil.copytree(AGENTS, self.root / "agents")

    def run_validate(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "agents" / "tools" / "validate.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.root),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def path(self, relative: str) -> pathlib.Path:
        return self.root / "agents" / relative

    def run_product(self, product: pathlib.Path | None = None, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "agents" / "tools" / "validate.py"),
                "--product",
                str(product or self.root),
                *arguments,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.root),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def write_board(self, status: str = "todo", **overrides) -> None:
        task = {
            "task": "T-01",
            "title": "x",
            "spec": "specs/f.spec.yaml",
            "owner": "backend-engineer",
            "status": status,
            "wave": 1,
            "deps": [],
            "branch": "agent/backend/T-01" if status != "todo" else None,
            "pr": None,
            "packet": None,
            "signatures_required": ["tech-lead-backend", "qa-lead", "product-owner"],
            "signatures": {"tech-lead-backend": None, "qa-lead": None, "product-owner": None},
            "notes": "",
        }
        task.update(overrides)
        (self.root / "backlog.json").write_text(
            json.dumps({"tasks": [task]}), encoding="utf-8"
        )

    # --- casos ------------------------------------------------------------

    def test_clean_copy_passes(self) -> None:
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)

    def test_hand_edited_card_fails(self) -> None:
        card = self.path("cards/qa-lead.yaml")
        card.write_text(card.read_text(encoding="utf-8") + "\n# tocada a mano\n", encoding="utf-8")

        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("cards desactualizadas", result.stdout)

    def test_permission_rule_violation_fails(self) -> None:
        permissions = self.path("permissions.json")
        data = json.loads(permissions.read_text(encoding="utf-8"))
        data["matrix"]["product-owner"]["product-code"] = "read"
        permissions.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no debe leer product-code", result.stdout)

    def test_signature_without_a_role_fails(self) -> None:
        delivery = self.path("policy/delivery.yaml")
        text = delivery.read_text(encoding="utf-8").replace(
            '"qa-lead (calidad)"', '"technical-lead (calidad)"'
        )
        delivery.write_text(text, encoding="utf-8")

        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("sin rol del registro", result.stdout)

    def test_invalid_yaml_fails(self) -> None:
        (self.path("templates/adr.yaml")).write_text("roto: [sin cerrar\n", encoding="utf-8")

        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("YAML inválido", result.stdout)

    def test_unquoted_list_item_fails(self) -> None:
        role = self.path("roles/qa-lead.yaml")
        text = role.read_text(encoding="utf-8").replace(
            "decides:\n", "decides:\n  - texto: con dos puntos\n", 1
        )
        role.write_text(text, encoding="utf-8")

        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no es texto", result.stdout)

    def test_registry_mismatch_fails(self) -> None:
        registry = self.path("registry.json")
        data = json.loads(registry.read_text(encoding="utf-8"))
        data["roles"] = [entry for entry in data["roles"] if entry["id"] != "qa-lead"]
        registry.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = self.run_validate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("registry.json no coincide", result.stdout)

    # --- modo producto ----------------------------------------------------

    def test_product_mode_accepts_a_coherent_board(self) -> None:
        self.write_board("todo")

        result = self.run_product()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sistema ·", result.stdout)
        self.assertIn("tablero ·", result.stdout)

    def test_product_mode_reports_a_broken_board(self) -> None:
        self.write_board("in_progress", branch=None, spec=None, packet=None)

        result = self.run_product()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("tablero · ERROR", result.stdout)
        self.assertIn("sin rama", result.stdout)

    def test_product_mode_requires_the_system_inside(self) -> None:
        empty = self.root / "sin-sistema"
        empty.mkdir()

        result = self.run_product(empty)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("copia el sistema en su raíz", result.stderr)

    def test_product_mode_requires_a_board(self) -> None:
        result = self.run_product()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no encuentro el tablero", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
