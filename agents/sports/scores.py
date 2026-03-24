"""Live scores and standings for Philadelphia sports teams via ESPN API.

ESPN API (free, no key):
  site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard
  site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team_id}
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

log = logging.getLogger("lumen.sports.scores")

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_TIMEOUT = 15.0
_HEADERS = {"User-Agent": "LumenSportsBot/1.0", "Accept": "application/json"}

# Philadelphia team IDs on ESPN
PHILLY_TEAMS = {
    "eagles":   {"sport": "football",  "league": "nfl",  "id": "21", "name": "Philadelphia Eagles"},
    "phillies": {"sport": "baseball",  "league": "mlb",  "id": "22", "name": "Philadelphia Phillies"},
    "sixers":   {"sport": "basketball","league": "nba",  "id": "20", "name": "Philadelphia 76ers"},
    "flyers":   {"sport": "hockey",    "league": "nhl",  "id": "15", "name": "Philadelphia Flyers"},
    "union":    {"sport": "soccer",    "league": "usa.1","id": "18", "name": "Philadelphia Union"},
}


@dataclass(slots=True)
class GameScore:
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    status: str  # pre, in, post
    detail: str  # "Q3 5:42", "Final", "7:05 PM ET"
    broadcast: str = ""
    is_philly_game: bool = False
    philly_team: str = ""


@dataclass(slots=True)
class TeamRecord:
    team: str
    wins: int
    losses: int
    ties: int = 0
    standing: str = ""  # "1st in NFC East"
    streak: str = ""


@dataclass(slots=True)
class SportsSnapshot:
    games_today: list[GameScore] = field(default_factory=list)
    records: dict[str, TeamRecord] = field(default_factory=dict)
    game_today_exists: bool = False


def _is_philly_team(name: str) -> tuple[bool, str]:
    """Check if a team name matches a Philly team."""
    name_lower = name.lower()
    for key, info in PHILLY_TEAMS.items():
        if key in name_lower or info["name"].lower() in name_lower or "philadelphia" in name_lower or "76ers" in name_lower:
            return True, key
    return False, ""


async def fetch_scoreboard(sport: str, league: str) -> list[GameScore]:
    """Fetch today's scoreboard for a league."""
    url = f"{_ESPN_BASE}/{sport}/{league}/scoreboard"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("ESPN scoreboard %s/%s failed: %s", sport, league, e)
            return []

    games = []
    for event in data.get("events", []):
        competition = event.get("competitions", [{}])[0]
        competitors = competition.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        home_name = home.get("team", {}).get("displayName", "?")
        away_name = away.get("team", {}).get("displayName", "?")

        status_obj = event.get("status", {})
        status_type = status_obj.get("type", {}).get("state", "pre")
        detail = status_obj.get("type", {}).get("shortDetail", "")

        broadcasts = competition.get("broadcasts", [])
        broadcast = ""
        if broadcasts:
            names = broadcasts[0].get("names", [])
            broadcast = ", ".join(names) if names else ""

        is_philly_home, philly_key_home = _is_philly_team(home_name)
        is_philly_away, philly_key_away = _is_philly_team(away_name)
        is_philly = is_philly_home or is_philly_away
        philly_key = philly_key_home or philly_key_away

        games.append(GameScore(
            home_team=home_name,
            away_team=away_name,
            home_score=int(home.get("score", "0") or "0"),
            away_score=int(away.get("score", "0") or "0"),
            status=status_type,
            detail=detail,
            broadcast=broadcast,
            is_philly_game=is_philly,
            philly_team=philly_key,
        ))

    return games


async def fetch_team_record(sport: str, league: str, team_id: str, team_key: str) -> TeamRecord | None:
    """Fetch a team's current record."""
    url = f"{_ESPN_BASE}/{sport}/{league}/teams/{team_id}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("ESPN team %s failed: %s", team_key, e)
            return None

    team = data.get("team", {})
    record_items = team.get("record", {}).get("items", [])
    if not record_items:
        return TeamRecord(team=team_key, wins=0, losses=0)

    overall = record_items[0]
    stats = {s["name"]: s["value"] for s in overall.get("stats", [])}

    wins = int(stats.get("wins", 0))
    losses = int(stats.get("losses", 0))
    ties = int(stats.get("ties", 0))

    standing = ""
    standing_summary = team.get("standingSummary", "")
    if standing_summary:
        standing = standing_summary

    streak = stats.get("streak", "")

    return TeamRecord(
        team=team_key, wins=wins, losses=losses, ties=ties,
        standing=standing, streak=str(streak),
    )


async def get_philly_snapshot() -> SportsSnapshot:
    """Get today's games and records for all Philly teams."""
    # Fetch all scoreboards concurrently
    leagues = set()
    for info in PHILLY_TEAMS.values():
        leagues.add((info["sport"], info["league"]))

    scoreboard_tasks = [
        fetch_scoreboard(sport, league) for sport, league in leagues
    ]
    record_tasks = [
        fetch_team_record(info["sport"], info["league"], info["id"], key)
        for key, info in PHILLY_TEAMS.items()
    ]

    all_results = await asyncio.gather(*scoreboard_tasks, *record_tasks, return_exceptions=True)

    # Separate results
    n_scoreboards = len(leagues)
    scoreboard_results = all_results[:n_scoreboards]
    record_results = all_results[n_scoreboards:]

    # Collect Philly games
    philly_games = []
    for result in scoreboard_results:
        if isinstance(result, Exception):
            continue
        for game in result:
            if game.is_philly_game:
                philly_games.append(game)

    # Collect records
    records = {}
    for result in record_results:
        if isinstance(result, Exception) or result is None:
            continue
        records[result.team] = result

    return SportsSnapshot(
        games_today=philly_games,
        records=records,
        game_today_exists=len(philly_games) > 0,
    )


def snapshot_to_text(snap: SportsSnapshot) -> str:
    """Format sports snapshot as plain text for LLM context."""
    lines = ["=== PHILADELPHIA SPORTS ===\n"]

    if snap.games_today:
        lines.append("TODAY'S GAMES:")
        for g in snap.games_today:
            if g.status == "pre":
                lines.append(f"  {g.away_team} @ {g.home_team} — {g.detail}")
                if g.broadcast:
                    lines.append(f"    Watch on: {g.broadcast}")
            elif g.status == "in":
                lines.append(f"  LIVE: {g.away_team} {g.away_score} @ {g.home_team} {g.home_score} — {g.detail}")
            elif g.status == "post":
                lines.append(f"  FINAL: {g.away_team} {g.away_score} @ {g.home_team} {g.home_score}")
        lines.append("")
    else:
        lines.append("No Philly games today.\n")

    if snap.records:
        lines.append("SEASON RECORDS:")
        for key, rec in snap.records.items():
            record_str = f"{rec.wins}-{rec.losses}"
            if rec.ties:
                record_str += f"-{rec.ties}"
            standing_str = f" ({rec.standing})" if rec.standing else ""
            streak_str = f" | Streak: {rec.streak}" if rec.streak else ""
            lines.append(f"  {PHILLY_TEAMS[key]['name']}: {record_str}{standing_str}{streak_str}")
        lines.append("")

    return "\n".join(lines)
