from __future__ import annotations

import ipaddress
import json
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


def normalize_public_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        raise ValueError("A website URL is required.")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS website URLs are supported.")
    if parsed.username or parsed.password:
        raise ValueError("Website URLs containing credentials are not allowed.")
    if not parsed.hostname:
        raise ValueError("The website URL has no valid hostname.")
    hostname = parsed.hostname.encode("idna").decode("ascii")
    netloc = hostname
    if parsed.port:
        netloc = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, ""))


def _is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    # is_global handles loopback/private/link-local ranges while allowing the
    # globally routable 64:ff9b::/96 DNS64 prefix used on this machine.
    return ip.is_global


def assert_public_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if hostname.casefold() == "localhost" or hostname.casefold().endswith(".localhost"):
        raise ValueError("Local and private network websites are not allowed.")
    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_address(str(literal)):
            raise ValueError("Local and private network websites are not allowed.")
        return
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve website hostname: {hostname}") from exc
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ValueError("The website resolves to a local or private network address.")


_DOM_SCRIPT = r"""
() => {
  const visibleText = (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, 18000);
  const text = element => (element.innerText || element.getAttribute('aria-label') || '').trim();
  const headings = [...document.querySelectorAll('h1,h2,h3')].slice(0, 80).map(el => ({
    level: el.tagName.toLowerCase(), text: text(el).slice(0, 240)
  }));
  const images = [...document.images];
  const controls = [...document.querySelectorAll('button,a,input,select,textarea')];
  return {
    title: document.title,
    language: document.documentElement.lang || '',
    description: document.querySelector('meta[name="description"]')?.content || '',
    canonical: document.querySelector('link[rel="canonical"]')?.href || '',
    headings,
    visibleText,
    metrics: {
      h1Count: document.querySelectorAll('h1').length,
      imageCount: images.length,
      imagesMissingAlt: images.filter(img => !img.hasAttribute('alt')).length,
      linkCount: document.links.length,
      emptyLinks: [...document.links].filter(link => !text(link)).length,
      unlabeledControls: controls.filter(el => !text(el) && !el.getAttribute('title')).length,
      landmarkCount: document.querySelectorAll('main,nav,header,footer,aside,[role="main"],[role="navigation"]').length,
      documentWidth: document.documentElement.scrollWidth,
      documentHeight: document.documentElement.scrollHeight
    }
  };
}
"""


@dataclass
class WebsiteAnalysisTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="analyze_website",
        description=(
            "Open a public website in an isolated rendered browser, inspect its text and DOM, "
            "and visually review desktop and mobile screenshots. Use for requests such as "
            "'how does this website look?' or 'review my portfolio'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Website URL or bare domain."},
                "question": {
                    "type": "string",
                    "description": "Optional review focus or question about the website.",
                },
            },
            "required": ["url"],
        },
    )

    navigation_timeout_ms: int = 30_000

    def _capture(self, url: str, directory: Path) -> tuple[list[Path], dict[str, Any], str]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Website analysis requires Playwright. Install the project dependencies first."
            ) from exc

        brave = shutil.which("brave") or shutil.which("brave-browser")
        if not brave:
            raise RuntimeError("Brave is not installed.")

        screenshots: list[Path] = []
        dom: dict[str, Any] = {}
        final_url = url
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=brave, headless=True)
            try:
                for name, viewport, mobile in (
                    ("desktop", {"width": 1440, "height": 900}, False),
                    ("mobile", {"width": 390, "height": 844}, True),
                ):
                    browser_context = browser.new_context(
                        viewport=viewport,
                        device_scale_factor=1,
                        is_mobile=mobile,
                        locale="en-US",
                    )
                    page = browser_context.new_page()
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    )
                    try:
                        page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        pass
                    final_url = page.url
                    normalized_final = normalize_public_url(final_url)
                    assert_public_url(normalized_final)
                    page_height = int(
                        page.evaluate("() => document.documentElement.scrollHeight || 0") or 0
                    )
                    screenshot_path = directory / f"{name}.jpg"
                    if 0 < page_height <= 10_000:
                        page.screenshot(
                            path=str(screenshot_path),
                            type="jpeg",
                            quality=76,
                            full_page=True,
                        )
                    else:
                        page.screenshot(
                            path=str(screenshot_path),
                            type="jpeg",
                            quality=80,
                            full_page=False,
                        )
                    screenshots.append(screenshot_path)
                    if name == "desktop":
                        evaluated = page.evaluate(_DOM_SCRIPT)
                        dom = evaluated if isinstance(evaluated, dict) else {}
                    browser_context.close()
            finally:
                browser.close()
        return screenshots, dom, final_url

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        try:
            url = normalize_public_url(str(arguments.get("url") or ""))
            assert_public_url(url)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if context.analyze_images is None:
            return {"success": False, "error": "The configured model cannot analyze images."}

        question = str(arguments.get("question") or "").strip()
        with TemporaryDirectory(prefix="thursday-website-") as temp_dir:
            try:
                screenshots, dom, final_url = self._capture(url, Path(temp_dir))
                prompt = (
                    "Review this rendered website using both screenshots. The first image is desktop "
                    "and the second is mobile. Give a concise summary, strengths, then prioritized "
                    "issues and concrete fixes under visual design, mobile/responsiveness, content, "
                    "accessibility, and SEO. Tie observations to visible or DOM evidence and say when "
                    "something cannot be verified.\n\n"
                    f"Requested focus: {question or 'General website and portfolio review'}\n"
                    f"Requested URL: {url}\nFinal URL: {final_url}\n"
                    f"Extracted page evidence:\n{json.dumps(dom, ensure_ascii=False)[:24000]}"
                )
                review = context.analyze_images(prompt, screenshots)
            except Exception as exc:  # noqa: BLE001 - return a useful tool failure
                return {"success": False, "error": f"Website analysis failed: {exc}"}
        return {
            "success": True,
            "url": url,
            "final_url": final_url,
            "viewports": ["desktop 1440x900", "mobile 390x844"],
            "analysis": review,
            "page_title": dom.get("title", ""),
        }


def get_tools() -> list[BaseTool]:
    return [WebsiteAnalysisTool()]
