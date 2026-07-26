"""Tests for QuickAnswerTool's parallel page fetching."""

import time

from assistant.tools.quick_answer_tool import QuickAnswerTool


class FakeSearchTool:
    def __init__(self, results):
        self._results = results

    def execute(self, arguments, context):
        return {"success": True, "results": self._results}


class FakeFetchTool:
    def __init__(self, pages: dict[str, str], delay: float = 0.0):
        self._pages = pages
        self._delay = delay
        self.fetched: list[str] = []

    def execute(self, arguments, context):
        url = arguments["url"]
        self.fetched.append(url)
        if self._delay:
            time.sleep(self._delay)
        if url not in self._pages:
            return {"success": False, "error": "boom"}
        return {"success": True, "text": self._pages[url]}


def _tool(results, pages, delay=0.0):
    tool = QuickAnswerTool()
    tool.search_tool = FakeSearchTool(results)
    tool.fetch_tool = FakeFetchTool(pages, delay)
    return tool


def test_parallel_fetch_prefers_rank_order():
    results = [
        {"url": "https://a.example", "snippet": "a"},
        {"url": "https://b.example", "snippet": "b"},
    ]
    pages = {
        "https://a.example": "Nothing relevant here at all. Just words.",
        "https://b.example": "The capital of France is Paris. Extra words to make it long.",
    }
    tool = _tool(results, pages)
    out = tool.execute({"question": "capital of France"}, None)
    assert out["success"]
    assert "Paris" in out["answer"]
    # Rank order: b was the first result with a match... a had none, b wins.
    assert out["source"] == "https://b.example"
    assert set(tool.fetch_tool.fetched) == {"https://a.example", "https://b.example"}


def test_fetches_run_concurrently():
    results = [{"url": f"https://{c}.example", "snippet": c} for c in "ab"]
    pages = {r["url"]: "Some page text without overlap words." for r in results}
    tool = _tool(results, pages, delay=1.0)
    start = time.perf_counter()
    tool.execute({"question": "unrelated question"}, None)
    elapsed = time.perf_counter() - start
    # Sequential would take ~2s; parallel should be ~1s.
    assert elapsed < 1.8


def test_snippet_fallback_when_fetches_fail():
    results = [{"url": "https://x.example", "snippet": "snippet answer here"}]
    tool = _tool(results, pages={})
    out = tool.execute({"question": "something"}, None)
    assert out["success"]
    assert out["answer"] == "snippet answer here"
