from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant.tools.file_tools import _resolve_path


class PathSandboxTests(unittest.TestCase):
    def test_allows_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "note.txt"
            f.write_text("hi", encoding="utf-8")
            resolved = _resolve_path(str(f), [str(root)])
            self.assertEqual(resolved, f.resolve())

    def test_blocks_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandbox"
            root.mkdir()
            outside = Path(tmp) / "secret.txt"
            outside.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                _resolve_path(str(outside), [str(root)])

    def test_empty_roots_unrestricted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.txt"
            f.write_text("z", encoding="utf-8")
            resolved = _resolve_path(str(f), [])
            self.assertTrue(resolved.exists())


if __name__ == "__main__":
    unittest.main()
