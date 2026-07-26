from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from assistant.tools.base import BaseTool, ToolMetadata
from .browser_tools import WebSearchTool, FetchPageTool


class QuickAnswerTool(BaseTool):
    """Tool to get quick answers to questions using web search."""
    metadata = ToolMetadata(
        name="quick_answer",
        description="Get a quick answer to a question by searching the web and extracting the most relevant information. Good for factual questions, definitions, conversions, etc.",
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to answer"
                }
            },
            "required": ["question"],
        },
    )

    def __init__(self):
        self.search_tool = WebSearchTool(max_results=3)
        self.fetch_tool = FetchPageTool()

    def _fetch_answer(self, url: str, question_words: set[str], context: Any) -> str:
        """Fetch one page and return the first sentence matching the question."""
        fetch_result = self.fetch_tool.execute(
            {"url": url, "max_chars": 2000},
            context,
        )
        if not fetch_result.get("success"):
            return ""
        content = fetch_result.get("text", "")
        sentences = [s.strip() for s in content.split('.') if len(s.strip()) > 20]
        for sentence in sentences[:10]:  # Check first 10 sentences
            sentence_words = set(sentence.lower().split())
            if len(question_words & sentence_words) >= min(2, len(question_words) // 2):
                return sentence
        return ""

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        question = arguments.get("question", "").strip()
        if not question:
            return {"success": False, "error": "Question is required"}

        try:
            # Search for the question
            search_result = self.search_tool.execute(
                {"query": question, "max_results": 3},
                context
            )

            if not search_result.get("success"):
                return {"success": False, "error": f"Search failed: {search_result.get('error')}"}

            results = search_result.get("results", [])
            if not results:
                return {"success": False, "error": "No search results found"}

            # Fetch the top 2 results in parallel and score sentences from each
            best_answer = ""
            best_source = ""
            question_words = set(question.lower().split())
            candidates = [r.get("url") for r in results[:2] if r.get("url")]

            if candidates:
                with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
                    answers = list(
                        pool.map(lambda u: self._fetch_answer(u, question_words, context), candidates)
                    )
                # Rank order wins (same semantics as the old sequential loop)
                for url, answer in zip(candidates, answers, strict=True):
                    if answer:
                        best_answer = answer
                        best_source = url
                        break

            # If we didn't find a good answer from content, use the search snippets
            if not best_answer and results:
                # Combine snippets from top results
                snippets = [r.get("snippet", "") for r in results if r.get("snippet")]
                if snippets:
                    best_answer = " ".join(snippets[:2])
                    best_source = results[0].get("url", "")
            
            if not best_answer:
                best_answer = "I found some search results but couldn't extract a clear answer. Try being more specific."
                best_source = results[0].get("url", "") if results else ""
            
            return {
                "success": True,
                "question": question,
                "answer": best_answer.strip(),
                "source": best_source,
                "sources_consulted": len(results)
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error getting quick answer: {str(e)}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [QuickAnswerTool()]