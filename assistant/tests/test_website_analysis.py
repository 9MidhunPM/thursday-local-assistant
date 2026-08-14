from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant.tools.website_analysis_tool import (
    WebsiteAnalysisTool,
    assert_public_url,
    normalize_public_url,
)


def test_normalize_bare_domain_and_remove_fragment():
    assert normalize_public_url("midhunpm.in#work") == "https://midhunpm.in/"


def test_private_and_non_http_urls_are_rejected():
    with pytest.raises(ValueError):
        normalize_public_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        assert_public_url("http://127.0.0.1/")


def test_website_analysis_passes_desktop_and_mobile_images_to_vision(monkeypatch):
    tool = WebsiteAnalysisTool()

    def fake_capture(url: str, directory: Path):
        desktop = directory / "desktop.jpg"
        mobile = directory / "mobile.jpg"
        desktop.write_bytes(b"desktop")
        mobile.write_bytes(b"mobile")
        return [desktop, mobile], {"title": "Portfolio", "metrics": {"h1Count": 1}}, url

    captured: dict[str, object] = {}

    def analyze(prompt: str, paths: list[Path]) -> str:
        captured["prompt"] = prompt
        captured["paths"] = [path.name for path in paths]
        return "Strong hierarchy; improve mobile contrast."

    monkeypatch.setattr(tool, "_capture", fake_capture)
    result = tool.execute(
        {"url": "https://93.184.216.34", "question": "Review the portfolio"},
        SimpleNamespace(analyze_images=analyze),  # type: ignore[arg-type]
    )
    assert result["success"]
    assert result["page_title"] == "Portfolio"
    assert captured["paths"] == ["desktop.jpg", "mobile.jpg"]
    assert "Review the portfolio" in str(captured["prompt"])

