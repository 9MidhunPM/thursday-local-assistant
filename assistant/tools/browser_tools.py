from __future__ import annotations

import asyncio
import html
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx
import trafilatura

from assistant.tools.base import BaseTool, ToolMetadata
from .spotify_tools import SpotifySearchPlayTool


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


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

        try:
            response = await client.get(url)
            response.raise_for_status()
            return self._parse_results(response.text)
        except Exception:
            return []

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
        try:
            response = await client.get(
                "https://www.googleapis.com/customsearch/v1", params=params
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            return []

        items = data.get("items", [])
        results: list[SearchResult] = []
        for item in items:
            title = item.get("title", "")
            url = item.get("link", "")
            snippet = item.get("snippet", "")
            if title and url:
                results.append(SearchResult(title=title, url=url, snippet=snippet))
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

        try:
            response = await client.get(
                self.api_endpoint, headers=headers, params=params
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            return []

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
                        results.append(SearchResult(title=title, url=url, snippet=snippet))
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
                    results.append(SearchResult(title=title, url=url, snippet=snippet))
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
                results.append(SearchResult(title=title, url=url, snippet=snippet))
                if len(results) >= self._max_results:
                    break
        return results

    # -----------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------
    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        query = arguments.get("query", "").strip()
        max_results = int(arguments.get("max_results", self._max_results))

        if not query:
            return {"success": False, "error": "Query is required"}

        # Determine which search to use: Google > Generic > DuckDuckGo
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context, run in executor with new loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                if self.use_google:
                    future = executor.submit(
                        lambda: asyncio.run(self._search_google(query))
                    )
                    results = future.result()
                    if not results and self.use_generic:
                        future = executor.submit(
                            lambda: asyncio.run(self._search_generic(query))
                        )
                        results = future.result()
                    if not results:
                        future = executor.submit(
                            lambda: asyncio.run(self._search_duckduckgo(query))
                        )
                        results = future.result()
                elif self.use_generic:
                    future = executor.submit(
                        lambda: asyncio.run(self._search_generic(query))
                    )
                    results = future.result()
                    if not results:
                        future = executor.submit(
                            lambda: asyncio.run(self._search_duckduckgo(query))
                        )
                        results = future.result()
                else:
                    future = executor.submit(
                        lambda: asyncio.run(self._search_duckduckgo(query))
                    )
                    results = future.result()
        except RuntimeError:
            # No running loop, safe to use asyncio.run directly
            if self.use_google:
                results = asyncio.run(self._search_google(query))
                if not results and self.use_generic:
                    results = asyncio.run(self._search_generic(query))
                if not results:
                    results = asyncio.run(self._search_duckduckgo(query))
            elif self.use_generic:
                results = asyncio.run(self._search_generic(query))
                if not results:
                    results = asyncio.run(self._search_duckduckgo(query))
            else:
                results = asyncio.run(self._search_duckduckgo(query))

        if not results:
            return {"success": False, "error": "No results found"}

        return {
            "success": True,
            "results": [
                {
                    "title": r.title,
                    "url": r.url,
                    "snippet": r.snippet,
                }
                for r in results[:max_results]
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
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _fetch_and_extract(self, url: str, max_chars: int = 8000) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.get(url)
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
                "url": url,
                "title": metadata.title if metadata and metadata.title else "",
                "text": extracted,
                "author": metadata.author if metadata else None,
                "date": metadata.date if metadata else None,
                "description": metadata.description if metadata else None,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        url = arguments.get("url", "").strip()
        max_chars = int(arguments.get("max_chars", 8000))

        if not url:
            return {"success": False, "error": "URL is required"}

        if not url.startswith(("http://", "https://")):
            return {"success": False, "error": "URL must start with http:// or https://"}

        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    lambda: asyncio.run(self._fetch_and_extract(url, max_chars))
                )
                result = future.result()
        except RuntimeError:
            result = asyncio.run(self._fetch_and_extract(url, max_chars))

        return result


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

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        query = arguments.get("query", "").strip()
        max_results = int(arguments.get("max_results", self.max_results))

        if not query:
            return {"success": False, "error": "Query is required"}

        # First search
        search_result = self.search_tool.execute({"query": query, "max_results": max_results}, context)
        if not search_result.get("success"):
            return search_result

        results = search_result.get("results", [])
        if not results:
            return {"success": False, "error": "No search results found"}

        # Fetch content for each result
        enriched_results = []
        for r in results:
            fetch_result = self.fetch_tool.execute({"url": r["url"], "max_chars": self.fetch_chars}, context)
            if fetch_result.get("success"):
                enriched_results.append(
                    {
                        "title": fetch_result.get("title") or r["title"],
                        "url": r["url"],
                        "snippet": r["snippet"],
                        "content": fetch_result.get("text", ""),
                        "author": fetch_result.get("author"),
                        "date": fetch_result.get("date"),
                    }
                )
            else:
                enriched_results.append(
                    {
                        "title": r["title"],
                        "url": r["url"],
                        "snippet": r["snippet"],
                        "content": "",
                        "error": fetch_result.get("error"),
                    }
                )

        return {
            "success": True,
            "query": query,
            "results": enriched_results,
        }


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [WebSearchTool(), FetchPageTool()]