"""The JSON the API returns, shaped for the token's role (docs/contract.md)."""

from dataclasses import asdict
from urllib.parse import urlsplit

from .rules import Counts, Window, derive
from .store import event_to_json, format_time

WHATSAPP_HOSTS = ("wa.me", "chat.whatsapp.com", "api.whatsapp.com")


def window_of(team, match):
    return Window.for_match(match.date, team.timezone)


def _counts(counts):
    return {"us": counts.us, "them": counts.them}


def _team(team):
    out = {
        "id": team.id,
        "name": team.name,
        "lang": team.lang,
        "colors": {"us": team.colors.us, "them": team.colors.them},
    }
    if team.contact is not None:
        out["contact"] = _contact(team)
    if team.demo:
        out["demo"] = True
    return out


def _contact(team):
    """The server's own path to the contact, so that its address (a phone number,
    often) is in no page and no answer; the path redirects to it."""
    host = urlsplit(team.contact).hostname
    return {"url": f"/chat/{team.id}", "whatsapp": host in WHATSAPP_HOSTS}


def contacts(teams):
    """The teams a visitor can ask about a wrong link, never with a token."""
    return [
        {"name": t.name, "lang": t.lang, "contact": _contact(t)}
        for t in teams
        if t.contact is not None
    ]


def _location(location):
    out = {"name": location.name}
    if location.address is not None:
        out["address"] = location.address
    return out


def _set(state):
    return {
        "set": state.set,
        "started_at": format_time(state.started_at),
        "ended_at": None if state.ended_at is None else format_time(state.ended_at),
        "closed": state.closed,
        "score": _counts(state.score),
        "timeouts": _counts(state.timeouts),
        "winner": state.winner,
    }


def _feed(events, current):
    score, feed = Counts(), []
    for event in events:
        if event.set == current and event.type == "point":
            score = score.plus(event.team)
            feed.append(
                {
                    "t": format_time(event.t),
                    "team": event.team,
                    "player": event.player,
                    "score": _counts(score),
                }
            )
    return feed[::-1]


def match_state(team, match, role, log, now):
    """The state of one match: the same for both roles but for what is noted."""
    window = window_of(team, match)
    state = derive(match.rules, window, log.events, now)
    out = {
        "role": role,
        "team": _team(team),
        "match": {
            "n": match.n,
            "date": match.date.isoformat(timespec="minutes"),
            "opponent": match.opponent,
            "location": _location(match.location),
            "status": state.status,
            "opens_at": format_time(window.opens_at),
            "locks_at": format_time(window.locks_at),
            "editable": state.editable,
        },
        "rules": asdict(match.rules),
        "sets": [_set(s) for s in state.sets],
        "sets_won": _counts(state.sets_won),
        "current": state.current,
    }
    if role == "staff":
        out["players"] = {p: team.roster[p] for p in match.players}
        out["events"] = [event_to_json(e) for e in log.events]
    elif team.show_scorers:
        out["players"] = {p: team.roster[p] for p in match.players}
        out["feed"] = _feed(log.events, state.current)
    return out


def callup(team, match, now):
    """The players of the team and those called up for a match, for the page that
    changes them."""
    closes = window_of(team, match).callup_closes_at
    return {
        "team": _team(team),
        "match": {
            "n": match.n,
            "date": match.date.isoformat(timespec="minutes"),
            "opponent": match.opponent,
        },
        "roster": dict(team.roster),
        "players": list(match.players),
        "closes_at": format_time(closes),
        "open": now < closes,
    }


def admin_teams(teams):
    """Every team with the links to hand out, for the one admin."""
    return [
        {
            "id": team_id,
            "name": teams.team(team_id).name,
            "links": {
                "family": f"/{roles['family']}",
                "staff": f"/{roles['staff']}",
                "coach": f"/{teams.coach_token(team_id)}",
            },
        }
        for team_id, roles in teams.tokens.items()
    ]


def team_info(team, raw, languages):
    """A team's information as the form that edits it shows it: from the file as it
    is (so a value the server would not show is not lost) and the languages to
    pick."""
    return {
        "id": team.id,
        "name": team.name,
        "lang": team.lang,
        "timezone": raw["timezone"],
        "colors": raw["colors"],
        "contact": raw.get("contact"),
        "show_scorers": team.show_scorers,
        "rules": raw["rules"],
        "roster": dict(team.roster),
        "languages": sorted(languages),
    }


def match_closed(team, match, now):
    """From the opening of its window a match is not changed any more, only
    scored."""
    return now >= window_of(team, match).opens_at


def season(team, store, now):
    """The matches of a team, as the page that edits the season shows them: those
    already open for scoring are read-only, and one with a record cannot be
    removed."""
    return {
        "team": _team(team),
        "matches": [
            {
                "n": m.n,
                "date": m.date.isoformat(timespec="minutes"),
                "opponent": m.opponent,
                "location": _location(m.location),
                "closed": match_closed(team, m, now),
                "has_log": bool(store.get(team.id, m.n).events),
            }
            for m in sorted(team.matches, key=lambda m: (m.date, m.n))
        ],
    }


def coach_dashboard(team, now):
    """What the coach's start page lists: each match with its call-up."""
    return {
        "team": _team(team),
        "matches": [
            {
                "n": m.n,
                "date": m.date.isoformat(timespec="minutes"),
                "opponent": m.opponent,
                "called": len(m.players),
                "callup_open": now < window_of(team, m).callup_closes_at,
            }
            for m in sorted(team.matches, key=lambda m: (m.date, m.n))
        ],
    }


def match_list(team, role, store, now):
    """The team's matches by date, each with what a list card shows."""
    matches = []
    for match in sorted(team.matches, key=lambda m: (m.date, m.n)):
        log = store.get(team.id, match.n)
        state = derive(match.rules, window_of(team, match), log.events, now)
        live = [s for s in state.sets if s.set == state.current and not s.closed]
        item = {
            "n": match.n,
            "date": match.date.isoformat(timespec="minutes"),
            "opponent": match.opponent,
            "location": _location(match.location),
            "status": state.status,
            "sets_won": _counts(state.sets_won),
            "current_set": live[0].set if state.status == "live" and live else None,
            "current_score": _counts(live[0].score)
            if state.status == "live" and live
            else None,
        }
        matches.append(item)
    return {"role": role, "team": _team(team), "matches": matches}
