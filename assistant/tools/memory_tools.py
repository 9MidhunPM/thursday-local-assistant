from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class StorePreferenceTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="store_preference",
        description="Store a user preference by key.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        key = arguments.get("key")
        value = arguments.get("value")
        if not isinstance(key, str) or not isinstance(value, str):
            return {"success": False, "error": "Key and value are required."}
        context.memory.set_preference(key, value, context.now().isoformat())
        return {"success": True, "output": "Preference stored."}


@dataclass
class GetPreferenceTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="get_preference",
        description="Retrieve a user preference by key.",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        key = arguments.get("key")
        if not isinstance(key, str):
            return {"success": False, "error": "Key is required."}
        pref = context.memory.get_preference(key)
        if pref is None:
            return {"success": False, "error": "Preference not found."}
        return {"success": True, "output": {"key": pref.key, "value": pref.value}}


@dataclass
class StoreMemoryTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="store_memory",
        description="Store a named memory.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["name", "content"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        name = arguments.get("name")
        content = arguments.get("content")
        if not isinstance(name, str) or not isinstance(content, str):
            return {"success": False, "error": "Name and content are required."}
        context.memory.store_memory(name, content, context.now().isoformat())
        return {"success": True, "output": "Memory stored."}


@dataclass
class RecallMemoryTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="recall_memory",
        description="Recall a named memory.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        name = arguments.get("name")
        if not isinstance(name, str):
            return {"success": False, "error": "Name is required."}
        memory = context.memory.recall_memory(name)
        if memory is None:
            return {"success": False, "error": "Memory not found."}
        return {"success": True, "output": {"name": memory.name, "content": memory.content}}


def get_tools() -> list[BaseTool]:
    return [
        StorePreferenceTool(),
        GetPreferenceTool(),
        DeletePreferenceTool(),
        StoreMemoryTool(),
        RecallMemoryTool(),
        DeleteMemoryTool(),
        StoreKnowledgeFactTool(),
        SearchPersonalKnowledgeTool(),
        GetEntityProfileTool(),
        DeleteFactTool(),
        DeleteEntityFactsTool(),
        ForgetEverythingTool(),
    ]


@dataclass
class StoreKnowledgeFactTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="store_fact",
        description="Store a personal knowledge graph fact such as person, project, preference, or relationship data.",
        parameters={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
            },
            "required": ["subject", "predicate", "object"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        subject = arguments.get("subject")
        predicate = arguments.get("predicate")
        object_value = arguments.get("object")
        if not all(isinstance(value, str) for value in (subject, predicate, object_value)):
            return {"success": False, "error": "Subject, predicate, and object are required."}
        context.memory.remember_fact(
            subject.strip(),
            predicate.strip(),
            object_value.strip(),
            context.now().isoformat(),
        )
        return {"success": True, "output": "Knowledge fact stored."}


@dataclass
class SearchPersonalKnowledgeTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="search_personal_knowledge",
        description="Search preferences, saved memories, and knowledge graph facts relevant to a topic.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        query = arguments.get("query")
        limit = int(arguments.get("limit", 4))
        if not isinstance(query, str):
            return {"success": False, "error": "Query is required."}
        return {"success": True, "output": context.memory.build_context(query, limit=limit)}


@dataclass
class GetEntityProfileTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="get_entity_profile",
        description="Get all known facts for a person, project, place, or other named entity.",
        parameters={
            "type": "object",
            "properties": {"subject": {"type": "string"}},
            "required": ["subject"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        subject = arguments.get("subject")
        if not isinstance(subject, str):
            return {"success": False, "error": "Subject is required."}
        facts = context.memory.get_entity_facts(subject)
        if not facts:
            return {"success": False, "error": "No facts found for that entity."}
        return {
            "success": True,
            "output": [
                {"subject": fact.subject, "predicate": fact.predicate, "object": fact.object}
                for fact in facts
            ],
        }


@dataclass
class DeletePreferenceTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="delete_preference",
        description="Delete a stored user preference by key.",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        key = arguments.get("key")
        if not isinstance(key, str):
            return {"success": False, "error": "Key is required."}
        deleted = context.memory.delete_preference(key)
        if not deleted:
            return {"success": False, "error": "Preference not found."}
        return {"success": True, "output": f"Preference '{key}' deleted."}


@dataclass
class DeleteMemoryTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="delete_memory",
        description="Delete a named memory.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        name = arguments.get("name")
        if not isinstance(name, str):
            return {"success": False, "error": "Name is required."}
        deleted = context.memory.delete_memory(name)
        if not deleted:
            return {"success": False, "error": "Memory not found."}
        return {"success": True, "output": f"Memory '{name}' deleted."}


@dataclass
class DeleteFactTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="delete_fact",
        description="Delete a specific knowledge fact by subject, predicate, and object.",
        parameters={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
            },
            "required": ["subject", "predicate", "object"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        subject = arguments.get("subject")
        predicate = arguments.get("predicate")
        object_value = arguments.get("object")
        if not all(isinstance(v, str) for v in (subject, predicate, object_value)):
            return {"success": False, "error": "Subject, predicate, and object are required."}
        deleted = context.memory.delete_fact(subject, predicate, object_value)
        if not deleted:
            return {"success": False, "error": "Fact not found."}
        return {"success": True, "output": "Fact deleted."}


@dataclass
class DeleteEntityFactsTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="delete_entity_facts",
        description="Delete all knowledge facts for a given entity (person, project, place, etc.).",
        parameters={
            "type": "object",
            "properties": {"subject": {"type": "string"}},
            "required": ["subject"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        subject = arguments.get("subject")
        if not isinstance(subject, str):
            return {"success": False, "error": "Subject is required."}
        count = context.memory.delete_entity_facts(subject)
        if count == 0:
            return {"success": False, "error": "No facts found for that entity."}
        return {"success": True, "output": f"Deleted {count} fact(s) for '{subject}'."}


@dataclass
class ForgetEverythingTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="forget_everything",
        description="Wipe ALL stored memory: preferences, named memories, and knowledge facts. Use with caution.",
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        result = context.memory.forget_everything()
        return {
            "success": True,
            "output": (
                f"Memory wiped: {result['preferences_deleted']} preferences, "
                f"{result['memories_deleted']} memories, "
                f"{result['facts_deleted']} facts deleted."
            ),
        }
