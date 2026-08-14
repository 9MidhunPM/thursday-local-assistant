from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.config.loader import TerminalSafetyConfig, ToolConfig
from assistant.tools.file_tools import FindFileSystemTool, RevealPathTool


def _config(read_roots: list[str]) -> ToolConfig:
    return ToolConfig(
        read_roots=read_roots,
        write_roots=read_roots,
        app_commands={},
        terminal=TerminalSafetyConfig(
            allow_shell=True,
            whitelist_commands=[],
            blacklist_patterns=[],
            confirm_patterns=[],
            timeout_sec=15,
        ),
    )


class FileSearchTests(unittest.TestCase):
    @patch("assistant.tools.file_tools.subprocess.run")
    @patch("assistant.tools.file_tools.shutil.which", return_value="/usr/bin/plocate")
    def test_plocate_results_are_numbered_and_ranked(self, _which, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                b"/usr/share/templates/resume-example.txt\0"
                b"/home/midhun/Documents/Resume.pdf\0"
            ),
            stderr=b"",
        )

        result = FindFileSystemTool(config=_config(["/"])).execute(
            {"filename": "find me my resume"}, None  # type: ignore[arg-type]
        )

        self.assertTrue(result["success"])
        output = result["output"]
        self.assertEqual(output["source"], "plocate")
        self.assertEqual(output["results"][0]["index"], 1)
        self.assertEqual(output["results"][0]["path"], "/home/midhun/Documents/Resume.pdf")

    @patch("assistant.tools.file_tools.os.walk")
    @patch("assistant.tools.file_tools.subprocess.run")
    @patch("assistant.tools.file_tools.shutil.which", return_value="/usr/bin/plocate")
    def test_broken_plocate_database_falls_back_to_live_walk(self, _which, run, walk) -> None:
        run.return_value = subprocess.CompletedProcess(
            [], 1, stdout=b"", stderr=b"database is not initialized"
        )
        walk.return_value = [("/home/midhun/Documents", [], ["Resume.pdf"])]

        result = FindFileSystemTool(config=_config(["/"])).execute(
            {"filename": "resume"}, None  # type: ignore[arg-type]
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"]["source"], "filesystem_walk")
        self.assertEqual(result["output"]["results"][0]["name"], "Resume.pdf")

    @patch("assistant.tools.file_tools.time.sleep")
    @patch("assistant.tools.file_tools.subprocess.Popen")
    @patch("assistant.tools.file_tools.shutil.which")
    def test_reveal_file_opens_parent_in_thunar(self, which, popen, _sleep) -> None:
        which.side_effect = lambda name: "/usr/bin/thunar" if name == "thunar" else None
        popen.return_value.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "resume final.pdf"
            selected.write_text("test", encoding="utf-8")
            result = RevealPathTool(config=_config([tmp])).execute(
                {"path": str(selected)}, None  # type: ignore[arg-type]
            )

        self.assertTrue(result["success"])
        command = popen.call_args.args[0]
        self.assertEqual(command, ["thunar", "--window", str(selected.parent)])


if __name__ == "__main__":
    unittest.main()
