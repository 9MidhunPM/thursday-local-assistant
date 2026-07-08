from __future__ import annotations

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
            
            # Try to get content from the top results to find a direct answer
            best_answer = ""
            best_source = ""
            
            for result in results[:2]:  # Check top 2 results
                url = result.get("url")
                if not url:
                    continue
                
                # Fetch the page content
                fetch_result = self.fetch_tool.execute(
                    {"url": url, "max_chars": 2000}, 
                    context
                )
                
                if fetch_result.get("success"):
                    content = fetch_result.get("text", "")
                    # Simple extraction: look for sentences that might answer the question
                    # This is a basic implementation - a more sophisticated one would use NLP
                    sentences = [s.strip() for s in content.split('.') if len(s.strip()) > 20]
                    
                    # Look for sentences containing keywords from the question
                    question_words = set(question.lower().split())
                    for sentence in sentences[:10]:  # Check first 10 sentences
                        sentence_words = set(sentence.lower().split())
                        # If there's good overlap, this might be a good answer
                        if len(question_words & sentence_words) >= min(2, len(question_words) // 2):
                            if len(sentence) > len(best_answer):
                                best_answer = sentence
                                best_source = url
                                break
                    
                    # If we found a good answer, break
                    if best_answer:
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