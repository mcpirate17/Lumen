"""Upcoming game schedules for Philadelphia teams via ESPN API."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from agents.sports.scores import PHILLY_TEAMS, _ESPN_BASE, _HEADERS, _TIMEOUT

log = logging.getLogger("lumen.sports.schedule")


@dataclass(slots=True)
class UpcomingGame:
    team: str
    opponent: str
    date: str
    time: str
    home_away: str  # "home" or "away"
    venue: str = ""
    broadcast: str = ""


async def fetch_upcoming(sport: str, league: str, team_id: str,
                          team_key: str, count: int = 5) -> list[UpcomingGame]:
    """Fetch upcoming games for a team."""
    url = f"{_ESPN_BASE}/{sport}/{league}/teams/{team_id}/schedule"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("ESPN schedule %s failed: %s", team_key, e)
            return []

    now = datetime.now(timezone.utc)
    upcoming = []

    for event in data.get("events", []):
        event_date = event.get("date", "")
        if not event_date:
            continue
        try:
            dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
        except ValueError:
            continue

        if dt < now:
            continue  # skip past games

        competition = event.get("competitions", [{}])[0]
        competitors = competition.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        home_name = home.get("team", {}).get("displayName", "?")
        away_name = away.get("team", {}).get("displayName", "?")

        is_home = "philadelphia" in home_name.lower() or team_key in home_name.lower() or "76ers" in home_name.lower()
        opponent = away_name if is_home else home_name

        venue = competition.get("venue", {}).get("fullName", "")
        broadcasts = competition.get("broadcasts", [])
        broadcast = ""
        if broadcasts:
            names = broadcasts[0].get("names", [])
            broadcast = ", ".join(names) if names else ""

        upcoming.append(UpcomingGame(
            team=team_key,
            opponent=opponent,
            date=dt.strftime("%a %b %d"),
            time=dt.strftime("%-I:%M %p ET"),
            home_away="home" if is_home else "away",
            venue=venue,
            broadcast=broadcast,
        ))

        if len(upcoming) >= count:
            break

    return upcoming


async def get_all_upcoming(count_per_team: int = 3) -> dict[str, list[UpcomingGame]]:
    """Get upcoming games for all Philly teams."""
    tasks = []
    keys = []
    for key, info in PHILLY_TEAMS.items():
        tasks.append(fetch_upcoming(info["sport"], info["league"], info["id"], key, count_per_team))
        keys.append(key)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    schedule = {}
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            log.warning("Schedule fetch failed for %s: %s", key, result)
            schedule[key] = []
        else:
            schedule[key] = result

    return schedule


def schedule_to_text(schedule: dict[str, list[UpcomingGame]]) -> str:
    """Format upcoming games as plain text."""
    lines = ["=== UPCOMING GAMES ===\n"]
    for key, games in schedule.items():
        team_name = PHILLY_TEAMS[key]["name"]
        if not games:
            lines.append(f"{team_name}: No upcoming games scheduled")
            continue
        lines.append(f"{team_name}:")
        for g in games:
            prefix = "vs" if g.home_away == "home" else "@"
            lines.append(f"  {g.date} {g.time} — {prefix} {g.opponent}")
            if g.venue and g.home_away == "away":
                lines.append(f"    at {g.venue}")
        lines.append("")
    return "\n".join(lines)
