from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class ValorantStatsTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="valorant_stats",
        description="Fetch a player's Valorant rank, match history, or detailed match statistics. Use this tool like an API explorer: start with 'match_history' to get recent Match IDs, then use 'match_details' to drill down into a specific game.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["profile", "match_history", "match_details"],
                    "description": "What data to fetch."
                },
                "name": {"type": "string", "description": "Riot ID Name (defaults to TiTaN)"},
                "tag": {"type": "string", "description": "Riot ID Tag (defaults to Bozo)"},
                "region": {"type": "string", "enum": ["ap", "na", "eu", "kr", "latam", "br"], "description": "Server region (defaults to ap)."},
                "match_id": {"type": "string", "description": "The specific Match ID to fetch details for. Required for 'match_details' action."}
            },
            "required": ["action"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        name = arguments.get("name", "TiTaN")
        tag = arguments.get("tag", "Bozo")
        region = arguments.get("region", "ap")
        match_id = arguments.get("match_id")
        
        api_key = os.getenv("HENRIKDEV_API_KEY", "")
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Authorization': api_key
        }
        
        def fetch(url: str) -> dict[str, Any]:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as response:
                    return json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if e.code == 401 or e.code == 403:
                    return {"success": False, "error": "API Key is missing or invalid. Check HenrikDev API."}
                return {"success": False, "error": f"HTTP Error fetching data: {e.code} {e.reason}"}
            except Exception as e:
                return {"success": False, "error": f"Failed to fetch data: {e}"}

        if action == "profile":
            url = f"https://api.henrikdev.xyz/valorant/v1/mmr/{region}/{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}"
            res = fetch(url)
            if not res.get("data"):
                return res
            data = res["data"]
            tier = data.get("currenttierpatched", "Unranked")
            rr = data.get("ranking_in_tier", 0)
            elo = data.get("elo", 0)
            return {"success": True, "output": f"Rank: {tier} ({rr} RR)\nTotal ELO: {elo}"}
            
        elif action == "match_history":
            url = f"https://api.henrikdev.xyz/valorant/v3/matches/{region}/{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}?size=5"
            res = fetch(url)
            if not res.get("data"):
                return res
                
            report = f"Recent 5 Matches for {name}#{tag}:\n\n"
            for match in res["data"]:
                m_id = match.get("metadata", {}).get("matchid")
                m_map = match.get("metadata", {}).get("map")
                m_mode = match.get("metadata", {}).get("mode")
                
                player = None
                for p in match.get("players", {}).get("all_players", []):
                    if p.get("name", "").lower() == name.lower() and p.get("tag", "").lower() == tag.lower():
                        player = p
                        break
                        
                if player:
                    stats = player.get("stats", {})
                    kills = stats.get("kills", 0)
                    deaths = stats.get("deaths", 0)
                    assists = stats.get("assists", 0)
                    char = player.get("character", "Unknown Agent")
                    team = player.get("team")
                    
                    team_data = match.get("teams", {}).get(team.lower(), {}) if team else {}
                    won = team_data.get("has_won", False)
                    result = "WIN" if won else "LOSS"
                    if m_mode.lower() == "Deathmatch":
                        result = "DM"
                    
                    report += f"- **[{result}]** {m_mode} on {m_map} | **Match ID:** `{m_id}`\n"
                    report += f"  Agent: {char} | KDA: {kills}/{deaths}/{assists}\n\n"
                    
            return {"success": True, "output": report}
            
        elif action == "match_details":
            if not match_id:
                return {"success": False, "error": "match_id is required."}
            url = f"https://api.henrikdev.xyz/valorant/v2/match/{match_id}"
            res = fetch(url)
            if not res.get("data"):
                return res
                
            data = res["data"]
            m_map = data.get("metadata", {}).get("map")
            rounds_played = data.get("metadata", {}).get("rounds_played")
            
            report = f"Detailed Match Data (ID: {match_id})\nMap: {m_map} | Rounds Played: {rounds_played}\n\n"
            
            report += "Player Performances:\n"
            players = data.get("players", {}).get("all_players", [])
            for p in players:
                p_name = p.get("name")
                p_tag = p.get("tag")
                p_team = p.get("team")
                char = p.get("character")
                stats = p.get("stats", {})
                score = stats.get("score", 0)
                kills = stats.get("kills", 0)
                deaths = stats.get("deaths", 0)
                assists = stats.get("assists", 0)
                hs = stats.get("headshots", 0)
                bodyshots = stats.get("bodyshots", 0)
                legshots = stats.get("legshots", 0)
                total_shots = hs + bodyshots + legshots
                hs_percent = round((hs / total_shots) * 100) if total_shots > 0 else 0
                
                report += f"[{p_team}] {p_name}#{p_tag} ({char})\n"
                report += f"  - Score: {score} | K/D/A: {kills}/{deaths}/{assists} | HS%: {hs_percent}%\n"
                
            return {"success": True, "output": report}

        return {"success": False, "error": "Unknown action."}


def get_tools() -> list[BaseTool]:
    return [ValorantStatsTool()]
