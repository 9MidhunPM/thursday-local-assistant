from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

DESKTOP_ID = "thursday-brave-mail.desktop"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def gmail_compose_url(mailto_url: str) -> str:
    parsed = urllib.parse.urlsplit(mailto_url)
    if parsed.scheme.casefold() != "mailto":
        raise ValueError("Expected a mailto: URL.")
    values = {
        key.casefold(): value
        for key, value in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()
    }
    recipient = urllib.parse.unquote(parsed.path)
    params: dict[str, str] = {"view": "cm", "fs": "1"}
    mappings = {
        "to": ", ".join(part for part in (recipient, *values.get("to", [])) if part),
        "cc": ", ".join(values.get("cc", [])),
        "bcc": ", ".join(values.get("bcc", [])),
        "su": "\n".join(values.get("subject", [])),
        "body": "\n".join(values.get("body", [])),
    }
    params.update({key: value for key, value in mappings.items() if value})
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"https://mail.google.com/mail/u/0/?{query}"


def open_mailto(mailto_url: str) -> None:
    binary = shutil.which("brave") or shutil.which("brave-browser")
    if not binary:
        raise RuntimeError("Brave is not installed.")
    subprocess.Popen(
        [binary, gmail_compose_url(mailto_url)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _desktop_quote(value: str) -> str:
    if not any(character.isspace() for character in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def desktop_entry(python: str | None = None) -> str:
    project_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    interpreter = python or (str(project_python) if project_python.is_file() else sys.executable)
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=Thursday Brave Mail",
            "Comment=Open mail links as populated Gmail drafts in Brave",
            f"Exec={_desktop_quote(interpreter)} -m assistant.integrations.mailto_handler %U",
            "Terminal=false",
            "NoDisplay=true",
            "MimeType=x-scheme-handler/mailto;",
            "Categories=Network;Email;",
            "",
        ]
    )


def install() -> Path:
    data_home = Path(
        os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    ).expanduser()
    applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    target = applications / DESKTOP_ID
    target.write_text(desktop_entry(), encoding="utf-8")
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run([updater, str(applications)], check=False, timeout=15)
    xdg_mime = shutil.which("xdg-mime")
    if not xdg_mime:
        raise RuntimeError("xdg-mime is not installed.")
    result = subprocess.run(
        [xdg_mime, "default", DESKTOP_ID, "x-scheme-handler/mailto"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not set the mailto handler.")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Open mailto links as Gmail drafts in Brave.")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("urls", nargs="*")
    args = parser.parse_args()
    if args.install:
        print(install())
        return
    if not args.urls:
        parser.error("a mailto: URL is required")
    for url in args.urls:
        open_mailto(url)


if __name__ == "__main__":
    main()
