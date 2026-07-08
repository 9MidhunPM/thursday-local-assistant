from __future__ import annotations

from typing import Any

from assistant.tools.base import BaseTool, ToolMetadata
from .browser_tools import WebSearchTool


class NewsTool(BaseTool):
    """Tool to get latest news headlines on a topic."""
    
    metadata = ToolMetadata(
        name="news",
        description="Get latest news headlines on a topic. Returns recent news articles with headlines and summaries.",
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic to get news about (e.g., 'technology', 'sports', 'business', 'AI', or leave empty for general news)"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of news items to return (default: 5)",
                    "default": 5
                }
            },
            "required": ["topic"],
        },
    )

    def __init__(self):
        self.news_tool = WebSearchTool(max_results=10)

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        topic = arguments.get("topic", "").strip()
        max_results = int(arguments.get("max_results", 5))
        
        if not topic:
            topic = "latest news"
        
        try:
            # Search for recent news
            search_query = f"{topic} news today latest"
            search_result = self.news_tool.execute(
                {"query": search_query, "max_results": max_results * 2},  # Get extra to filter
                context
            )
            
            if not search_result.get("success"):
                return {"success": False, "error": f"Failed to fetch news: {search_result.get('error')}"}
            
            results = search_result.get("results", [])
            
            # Filter and format results
            news_items = []
            for result in results[:max_results]:
                title = result.get("title", "").strip()
                snippet = result.get("snippet", "").strip()
                url = result.get("url", "")
                
                if title and len(title) > 10:  # Basic filter for meaningful headlines
                    news_items.append({
                        "title": title,
                        "summary": snippet[:200] + "..." if len(snippet) > 200 else snippet,
                        "url": url
                    })
            
            if not news_items:
                return {"success": False, "error": f"No news found for topic: {topic}"}
            
            return {
                "success": True,
                "topic": topic,
                "count": len(news_items),
                "results": news_items
            }
        except Exception as e:
            return {"success": False, "error": f"Error fetching news: {str(e)}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [NewsTool()]