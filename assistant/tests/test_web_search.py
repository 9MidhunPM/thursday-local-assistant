from types import SimpleNamespace

import httpx

from assistant.tools.browser_tools import SearchResult, WebSearchTool


def test_search_falls_back_with_sanitized_diagnostics_and_deduplicates():
    tool = WebSearchTool(max_results=5)
    tool.use_google = True
    tool.use_generic = True

    async def google(query: str):
        request = httpx.Request("GET", "https://google.example/?key=do-not-log")
        raise httpx.HTTPStatusError(
            "credential-bearing URL", request=request, response=httpx.Response(403, request=request)
        )

    async def generic(query: str):
        return [
            SearchResult("One", "https://example.com/page?utm_source=x", "a", "generic"),
            SearchResult("Duplicate", "https://example.com/page", "b", "generic"),
        ]

    tool._search_google = google  # type: ignore[method-assign]
    tool._search_generic = generic  # type: ignore[method-assign]
    result = tool.execute({"query": "test"}, SimpleNamespace())
    assert result["success"]
    assert result["provider"] == "generic"
    assert result["attempts"][0] == {
        "provider": "google_cse",
        "status": "error",
        "error": "HTTP 403",
    }
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://example.com/page"

