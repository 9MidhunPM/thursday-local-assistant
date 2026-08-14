from __future__ import annotations

import asyncio
import html
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx
import trafilatura

from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.browser_control import BraveController
from assistant.tools.website_analysis_tool import assert_public_url, normalize_public_url


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str = "unknown"


def _canonical_result_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = urllib.parse.urlencode(
        [
            (key, value)
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in {"fbclid", "gclid", "ref"}
        ]
    )
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path or "/", query, "")
    )


def _safe_provider_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    if isinstance(exc, httpx.RequestError):
        return type(exc).__name__
    return type(exc).__name__


def _run_async(factory: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(factory())).result()


class WebSearchTool(BaseTool):
    metadata = ToolMetadata(
        name="web_search",
        description="Search the web and return results with title, URL, and snippet.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum number of results (default: 5)", "default": 5},
            },
            "required": ["query"],
        },
    )

    def __init__(self, max_results: int = 5, timeout: int = 15):
        self._max_results = max_results
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

        # Google Custom Search credentials
        self.google_api_key = os.getenv("GOOGLE_CSE_API_KEY")
        self.google_cx = os.getenv("GOOGLE_CSE_CX")
        self.use_google = bool(self.google_api_key and self.google_cx)

        # Generic bearer-token API
        self.api_key = os.getenv("SEARCH_API_KEY")
        self.api_endpoint = os.getenv("SEARCH_API_ENDPOINT")
        self.use_generic = bool(self.api_key and self.api_endpoint)
        # For searchapi.io, we may want to specify engine via env
        self.searchapi_engine = os.getenv("SEARCHAPI_ENGINE", "google")  # default to google

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # -----------------------------------------------------------------
    # DuckDuckGo fallback (original implementation)
    # -----------------------------------------------------------------
    def _parse_results(self, html_content: str) -> list[SearchResult]:
        """Parse DuckDuckGo HTML results."""
        results: list[SearchResult] = []

        from html.parser import HTMLParser

        class DDGParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results: list[dict[str, str]] = []
                self.current: dict[str, str] = {}
                self.in_result = False
                self.current_tag = ""
                self.href = ""

            @staticmethod
            def _extract_actual_url(redirect_url: str) -> str:
                """Extract actual URL from DuckDuckGo redirect."""
                if "duckduckgo.com/l/?uddg=" in redirect_url:
                    import urllib.parse

                    parsed = urllib.parse.urlparse(redirect_url)
                    query = urllib.parse.parse_qs(parsed.query)
                    if "uddg" in query:
                        return urllib.parse.unquote(query["uddg"][0])
                return redirect_url

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "a" and attrs_dict.get("class") == "result__snippet":
                    raw_url = attrs_dict.get("href", "")
                    self.current = {"url": self._extract_actual_url(raw_url), "snippet": ""}
                    self.in_result = True
                    self.current_tag = "snippet"
                elif tag == "a" and attrs_dict.get("class") == "result__url":
                    self.current_tag = "url"
                elif tag == "a" and attrs_dict.get("class") == "result__title":
                    self.current_tag = "title"
                    raw_url = attrs_dict.get("href", "")
                    self.href = self._extract_actual_url(raw_url)

            def handle_endtag(self, tag):
                if tag == "a" and self.in_result and "snippet" in self.current:
                    if self.current.get("snippet"):
                        self.results.append(self.current.copy())
                    self.current = {}
                    self.in_result = False

            def handle_data(self, data):
                if self.in_result and self.current_tag:
                    self.current[self.current_tag] = (self.current.get(self.current_tag, "") + data).strip()
                elif self.current_tag == "title" and self.href:
                    self.current["title"] = (self.current.get("title", "") + data).strip()

        parser = DDGParser()
        try:
            parser.feed(html_content)
            for r in parser.results[: self._max_results]:
                if r.get("snippet"):
                    results.append(
                        SearchResult(
                            title=r.get("title", r.get("snippet", "")[:80]),
                            url=r.get("url", ""),
                            snippet=r.get("snippet", ""),
                            provider="duckduckgo",
                        )
                    )
        except Exception:
            pass

        # Fallback: regex-based extraction
        if not results:
            link_pattern = r'href="(https?://[^"]+)"[^>]*>([^<]+)</a>'
            for match in re.finditer(link_pattern, html_content):
                url, title = match.groups()
                if url and title and len(title) > 5:
                    if any(
                        skip in url
                        for skip in ["duckduckgo.com", "javascript:", "#", "?q="]
                    ):
                        continue
                    results.append(
                        SearchResult(
                            title=html.unescape(title.strip()),
                            url=url,
                            snippet="",
                            provider="duckduckgo",
                        )
                    )
                if len(results) >= self._max_results:
                    break

        return results

    async def _search_duckduckgo(self, query: str) -> list[SearchResult]:
        """Search DuckDuckGo HTML endpoint."""
        client = await self._get_client()
        params = {
            "q": query,
            "kl": "us-en",
            "html": "1",
            "nojs": "1",
        }
        url = f"https://html.duckduckgo.com/html/?{urllib.parse.urlencode(params)}"

        response = await client.get(url)
        response.raise_for_status()
        return self._parse_results(response.text)

    # -----------------------------------------------------------------
    # Google Custom Search implementation
    # -----------------------------------------------------------------
    async def _search_google(self, query: str) -> list[SearchResult]:
        """Search using Google Custom Search JSON API."""
        if not self.use_google:
            return []

        client = await self._get_client()
        params = {
            "key": self.google_api_key,
            "cx": self.google_cx,
            "q": query,
            "num": min(self._max_results, 10),  # API max 10 per request
            "safe": "off",
        }
        response = await client.get(
            "https://www.googleapis.com/customsearch/v1", params=params
        )
        response.raise_for_status()
        data = response.json()

        items = data.get("items", [])
        results: list[SearchResult] = []
        for item in items:
            title = item.get("title", "")
            url = item.get("link", "")
            snippet = item.get("snippet", "")
            if title and url:
                results.append(
                    SearchResult(title=title, url=url, snippet=snippet, provider="google_cse")
                )
                if len(results) >= self._max_results:
                    break
        return results

    # -----------------------------------------------------------------
    # Generic bearer-token API
    # -----------------------------------------------------------------
    async def _search_generic(self, query: str) -> list[SearchResult]:
        """Search using a generic bearer-token API endpoint."""
        if not self.use_generic:
            return []

        client = await self._get_client()
        # Detect if using searchapi.io
        is_searchapi = "searchapi.io" in self.api_endpoint

        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        params = {
            "q": query,
            "num": self._max_results,
        }

        if is_searchapi:
            # searchapi.io expects engine and api_key as query params
            params.update(
                {
                    "engine": self.searchapi_engine,
                    "api_key": self.api_key,
                }
            )
            # No Authorization header for searchapi.io
        else:
            # Default bearer token
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = await client.get(
            self.api_endpoint, headers=headers, params=params
        )
        response.raise_for_status()
        data = response.json()

        results: list[SearchResult] = []

        if is_searchapi:
            # Handle SearchAPI.io specific responses
            # Prefer organic_results if present
            if "organic_results" in data and isinstance(data["organic_results"], list):
                for item in data["organic_results"]:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title") or ""
                    url = item.get("link") or item.get("url") or ""
                    snippet = item.get("snippet") or item.get("description") or ""
                    if title and url:
                        results.append(
                            SearchResult(title=title, url=url, snippet=snippet, provider="generic")
                        )
                        if len(results) >= self._max_results:
                            break
                if results:
                    return results
            # Fallback to text_blocks + (reference_links or markdown)
            if "text_blocks" in data:
                text_blocks = data.get("text_blocks", [])
                refs = data.get("reference_links", [])
                markdown = data.get("markdown", "")
                # Determine a default URL from reference_links if available
                default_url = "#"
                if refs and isinstance(refs, list) and len(refs) > 0:
                    first_ref = refs[0]
                    if isinstance(first_ref, dict):
                        default_url = first_ref.get("link", "#")
                # Regex to extract URLs from markdown: [text](url)
                url_pattern = re.compile(r'\[[^\]]*\]\((https?://[^)]+)\)')
                markdown_urls = url_pattern.findall(markdown) if markdown else []
                # If we have markdown URLs, we can use them; otherwise use default_url
                url_idx = 0
                for block in text_blocks:
                    if not isinstance(block, dict):
                        continue
                    answer = block.get("answer") or block.get("snippet") or ""
                    if not answer:
                        continue
                    # Determine title: first line or first 80 chars
                    lines = answer.split("\n")
                    title = lines[0].strip() if lines else answer.strip()
                    if len(title) > 80:
                        title = title[:80].rsplit(" ", 1)[0] + "..."
                    # Choose URL: prefer mapping via reference_indexes if present,
                    # else use markdown_urls in order, else fallback to default_url
                    url = "#"
                    if "reference_indexes" in block and isinstance(block["reference_indexes"], list):
                        # Try to map first index to link via refs
                        idx_list = block["reference_indexes"]
                        if idx_list and isinstance(idx_list[0], int):
                            idx = idx_list[0]
                            # Find matching ref by index
                            for ref in refs:
                                if isinstance(ref, dict) and ref.get("index") == idx:
                                    url = ref.get("link", "#")
                                    break
                    if url == "#" and markdown_urls and url_idx < len(markdown_urls):
                        url = markdown_urls[url_idx]
                        url_idx += 1
                    if url == "#":
                        url = default_url
                    # Use answer as snippet (maybe truncate)
                    snippet = answer
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "..."
                    results.append(
                        SearchResult(title=title, url=url, snippet=snippet, provider="generic")
                    )
                    if len(results) >= self._max_results:
                        break
                if results:
                    return results
            # If still no results, fall through to generic parsing

        # Generic extraction (used for non-searchapi or as fallback)
        items = []
        if isinstance(data, dict):
            # Common keys: items, results, data, organic_results
            items = (
                data.get("items")
                or data.get("results")
                or data.get("data")
                or data.get("organic_results")
                or []
            )
        elif isinstance(data, list):
            items = data

        for item in items:
            if not isinstance(item, dict):
                continue
            title = (
                item.get("title")
                or item.get("name")
                or item.get("title")
                or ""
            )
            url = (
                item.get("url")
                or item.get("link")
                or item.get("link")
                or ""
            )
            snippet = (
                item.get("snippet")
                or item.get("description")
                or item.get("snippet")
                or ""
            )
            if title and url:
                results.append(
                    SearchResult(title=title, url=url, snippet=snippet, provider="generic")
                )
                if len(results) >= self._max_results:
                    break
        return results

    # -----------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------
    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        query = arguments.get("query", "").strip()
        max_results = max(1, min(int(arguments.get("max_results", self._max_results)), 10))

        if not query:
            return {"success": False, "error": "Query is required"}

        providers: list[tuple[str, Any]] = []
        if self.use_google:
            providers.append(("google_cse", self._search_google))
        if self.use_generic:
            providers.append(("generic", self._search_generic))
        providers.append(("duckduckgo", self._search_duckduckgo))

        async def search_with_fallback() -> tuple[list[SearchResult], str, list[dict[str, Any]]]:
            attempts: list[dict[str, Any]] = []
            try:
                for provider_name, search in providers:
                    try:
                        candidate_results = await search(query)
                        attempts.append(
                            {
                                "provider": provider_name,
                                "status": "ok" if candidate_results else "no_results",
                                "result_count": len(candidate_results),
                            }
                        )
                    except Exception as exc:  # noqa: BLE001 - sanitized below
                        attempts.append(
                            {
                                "provider": provider_name,
                                "status": "error",
                                "error": _safe_provider_error(exc),
                            }
                        )
                        continue
                    if candidate_results:
                        return candidate_results, provider_name, attempts
                return [], "", attempts
            finally:
                await self.close()

        results, chosen_provider, attempts = _run_async(search_with_fallback)

        if not results:
            return {
                "success": False,
                "error": "No search provider returned results.",
                "attempts": attempts,
            }

        deduplicated: list[SearchResult] = []
        seen_urls: set[str] = set()
        for result in results:
            canonical = _canonical_result_url(result.url)
            if not canonical or canonical in seen_urls:
                continue
            seen_urls.add(canonical)
            result.url = canonical
            deduplicated.append(result)

        return {
            "success": True,
            "query": query,
            "provider": chosen_provider,
            "attempts": attempts,
            "results": [
                {
                    "title": r.title,
                    "url": r.url,
                    "snippet": r.snippet,
                    "provider": r.provider,
                }
                for r in deduplicated[:max_results]
            ],
        }


class FetchPageTool(BaseTool):
    metadata = ToolMetadata(
        name="fetch_page",
        description="Fetch and extract readable text content from a web page URL.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_chars": {"type": "integer", "description": "Maximum characters to return (default: 8000)", "default": 8000},
            },
            "required": ["url"],
        },
    )

    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _fetch_and_extract(self, url: str, max_chars: int = 8000) -> dict[str, Any]:
        client = await self._get_client()
        try:
            current_url = url
            for _ in range(6):
                response = await client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        break
                    current_url = normalize_public_url(
                        urllib.parse.urljoin(str(response.url), location)
                    )
                    assert_public_url(current_url)
                    continue
                break
            else:
                return {"success": False, "error": "Too many redirects."}
            response.raise_for_status()

            html_content = response.text

            # Use trafilatura for clean extraction
            extracted = trafilatura.extract(
                html_content,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                output_format="txt",
            )

            # Also try to get metadata
            metadata = trafilatura.extract_metadata(html_content)

            if not extracted:
                # Fallback: basic text extraction
                from html.parser import HTMLParser

                class SimpleParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.text_parts: list[str] = []
                        self.skip_tags = {"script", "style", "noscript", "iframe"}
                        self.in_skip = False

                    def handle_starttag(self, tag, attrs):
                        if tag in self.skip_tags:
                            self.in_skip = True

                    def handle_endtag(self, tag):
                        if tag in self.skip_tags:
                            self.in_skip = False

                    def handle_data(self, data):
                        if not self.in_skip and data.strip():
                            self.text_parts.append(data.strip())

                parser = SimpleParser()
                parser.feed(html_content)
                extracted = " ".join(parser.text_parts)

            if len(extracted) > max_chars:
                extracted = extracted[:max_chars] + "..."

            return {
                "success": True,
                "url": current_url,
                "title": metadata.title if metadata and metadata.title else "",
                "text": extracted,
                "author": metadata.author if metadata else None,
                "date": metadata.date if metadata else None,
                "description": metadata.description if metadata else None,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        raw_url = arguments.get("url", "").strip()
        max_chars = int(arguments.get("max_chars", 8000))

        if not raw_url:
            return {"success": False, "error": "URL is required"}

        try:
            url = normalize_public_url(raw_url)
            assert_public_url(url)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        async def fetch_once() -> dict[str, Any]:
            try:
                return await self._fetch_and_extract(url, max_chars)
            finally:
                await self.close()

        return _run_async(fetch_once)


class SearchAndFetchTool(BaseTool):
    metadata = ToolMetadata(
        name="search_and_fetch",
        description="Search the web and fetch full content of top results in one call. Returns structured data with title, URL, snippet, and full extracted text.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum number of results to fetch (default: 3)", "default": 3},
            },
            "required": ["query"],
        },
    )

    def __init__(self, max_results: int = 3, fetch_chars: int = 6000, timeout: int = 30):
        self.max_results = max_results
        self.fetch_chars = fetch_chars
        self.search_tool = WebSearchTool(max_results=max_results, timeout=timeout)
        self.fetch_tool = FetchPageTool(timeout=timeout)
        self.timeout = timeout

    def _new_fetch_tool(self) -> FetchPageTool:
        return FetchPageTool(timeout=self.timeout)

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        query = arguments.get("query", "").strip()
        max_results = max(1, min(int(arguments.get("max_results", self.max_results)), 5))

        if not query:
            return {"success": False, "error": "Query is required"}

        # First search
        search_result = self.search_tool.execute({"query": query, "max_results": max_results}, context)
        if not search_result.get("success"):
            return search_result

        results = search_result.get("results", [])
        if not results:
            return {"success": False, "error": "No search results found"}

        def enrich(r: dict[str, Any]) -> dict[str, Any]:
            # Each worker owns its AsyncClient/event loop.
            fetch_tool = self._new_fetch_tool()
            fetch_result = fetch_tool.execute(
                {"url": r["url"], "max_chars": self.fetch_chars}, context
            )
            if fetch_result.get("success"):
                return {
                    "title": fetch_result.get("title") or r["title"],
                    "url": fetch_result.get("url") or r["url"],
                    "snippet": r["snippet"],
                    "content": fetch_result.get("text", ""),
                    "author": fetch_result.get("author"),
                    "date": fetch_result.get("date"),
                    "provider": r.get("provider"),
                }
            return {
                "title": r["title"],
                "url": r["url"],
                "snippet": r["snippet"],
                "content": "",
                "provider": r.get("provider"),
                "error": fetch_result.get("error"),
            }

        workers = min(4, len(results))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            enriched_results = list(executor.map(enrich, results))

        return {
            "success": True,
            "query": query,
            "provider": search_result.get("provider"),
            "attempts": search_result.get("attempts", []),
            "results": enriched_results,
        }


class OpenGoogleSearchTool(BaseTool):
    metadata = ToolMetadata(
        name="open_google_search",
        description=(
            "Open Google search results in the user's existing Brave window and bring Brave to focus. "
            "Use only when the user asks to see or open Google results."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Google search query."}},
            "required": ["query"],
        },
    )

    def __init__(self, controller: BraveController | None = None):
        self.controller = controller or BraveController()

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "A Google search query is required."}
        url = f"https://www.google.com/search?{urllib.parse.urlencode({'q': query})}"
        opened, error = self.controller.open_url(url, title_hint="Google Search")
        if not opened:
            return {"success": False, "error": error or "Google could not be opened."}
        return {"success": True, "query": query, "url": url, "output": "Google results opened in Brave."}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [WebSearchTool(), FetchPageTool(), SearchAndFetchTool(), OpenGoogleSearchTool()]
