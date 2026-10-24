import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_teams import VALID

from match_tracker.rules import Event
from match_tracker.server import create_app
from match_tracker.store import Log, Store

# match 1 is 15:00 in Rome on 10 October, which is 13:00 UTC
KICKOFF = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)


def make_client(tmp_path, now=KICKOFF, **team):
    (tmp_path / "teams").mkdir(exist_ok=True)
    raw = copy.deepcopy(VALID) | team
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(raw))
    app = create_app(tmp_path / "teams", tmp_path / "data", now=lambda: now)
    return TestClient(app), app.state.teams.tokens["rho"]


def log_of(tmp_path, n, *events):
    Store(tmp_path / "data").put("rho", n, Log(len(events) + 1, events))


def at(seconds, set_, type_, team=None):
    return Event(0, KICKOFF + timedelta(seconds=seconds), set_, type_, team)


PLAYED = tuple(
    replace(event, id=i)
    for i, event in enumerate(
        (
            at(0, 1, "set_start"),
            at(10, 1, "point", "us"),
            at(20, 1, "point", "them"),
            at(30, 1, "point", "us"),
            at(40, 1, "timeout", "them"),
        ),
        start=1,
    )
)


def test_the_list_of_a_family_token(tmp_path):
    client, tokens = make_client(tmp_path)
    answer = client.get(f"/api/{tokens['family']}")
    assert answer.status_code == 200
    body = answer.json()
    assert body["role"] == "family"
    assert body["team"] == {
        "id": "rho",
        "name": "Volley Test",
        "lang": "it",
        "colors": {"us": "#111111", "them": "#eeeeee"},
    }
    assert [m["n"] for m in body["matches"]] == [1, 2]
    first = body["matches"][0]
    assert first == {
        "n": 1,
        "date": "2026-10-10T15:00",
        "opponent": "Opponent",
        "location": {"name": "Gym", "address": "Via Roma 1, Milano"},
        "status": "upcoming",
        "sets_won": {"us": 0, "them": 0},
        "current_set": None,
        "current_score": None,
    }
    assert body["matches"][1]["location"] == {"name": "Hall"}


def test_a_live_match_shows_its_set_score_in_the_list(tmp_path):
    log_of(tmp_path, 1, *PLAYED)
    client, tokens = make_client(tmp_path)
    first = client.get(f"/api/{tokens['staff']}").json()["matches"][0]
    assert first["status"] == "live"
    assert first["current_set"] == 1
    assert first["current_score"] == {"us": 2, "them": 1}


def test_the_state_of_a_match_for_staff(tmp_path):
    log_of(tmp_path, 1, *PLAYED)
    client, tokens = make_client(tmp_path)
    body = client.get(f"/api/{tokens['staff']}/1").json()
    assert body["role"] == "staff"
    assert body["match"] == {
        "n": 1,
        "date": "2026-10-10T15:00",
        "opponent": "Opponent",
        "location": {"name": "Gym", "address": "Via Roma 1, Milano"},
        "status": "live",
        "opens_at": "2026-10-10T12:00:00.000Z",
        "locks_at": "2026-10-11T13:00:00.000Z",
        "editable": True,
    }
    assert body["rules"]["best_of"] == 5
    assert "players" not in body
    assert body["current"] == 1 and body["sets_won"] == {"us": 0, "them": 0}
    assert body["sets"] == [
        {
            "set": 1,
            "started_at": "2026-10-10T13:00:00.000Z",
            "ended_at": None,
            "closed": False,
            "score": {"us": 2, "them": 1},
            "timeouts": {"us": 0, "them": 1},
            "winner": None,
        }
    ]
    assert [e["id"] for e in body["events"]] == [1, 2, 3, 4, 5]
    assert body["events"][1] == {
        "id": 2,
        "t": "2026-10-10T13:00:10.000Z",
        "set": 1,
        "type": "point",
        "team": "us",
    }


def test_the_family_state_has_no_events_no_feed_and_no_names(tmp_path):
    log_of(tmp_path, 1, *PLAYED)
    client, tokens = make_client(tmp_path)
    body = client.get(f"/api/{tokens['family']}/1").json()
    assert body["role"] == "family" and "events" not in body
    assert "players" not in body and "feed" not in body


def test_the_family_timeline_has_the_points_and_timeouts_of_every_set(tmp_path):
    log_of(tmp_path, 1, *PLAYED)
    client, tokens = make_client(tmp_path)
    body = client.get(f"/api/{tokens['family']}/1").json()
    assert body["timeline"] == [
        {"set": 1, "t": "2026-10-10T13:00:10.000Z", "type": "point", "team": "us"},
        {"set": 1, "t": "2026-10-10T13:00:20.000Z", "type": "point", "team": "them"},
        {"set": 1, "t": "2026-10-10T13:00:30.000Z", "type": "point", "team": "us"},
        {"set": 1, "t": "2026-10-10T13:00:40.000Z", "type": "timeout", "team": "them"},
    ]
    assert "timeline" not in client.get(f"/api/{tokens['staff']}/1").json()


def test_after_the_lock_the_match_is_over_and_not_editable(tmp_path):
    log_of(tmp_path, 1, *PLAYED)
    client, tokens = make_client(tmp_path, now=KICKOFF + timedelta(hours=25))
    body = client.get(f"/api/{tokens['family']}/1").json()
    assert body["match"]["status"] == "over" and not body["match"]["editable"]
    assert body["sets"][0]["closed"] and body["sets"][0]["winner"] == "us"
    assert body["sets"][0]["ended_at"] == "2026-10-11T13:00:00.000Z"
    assert body["sets_won"] == {"us": 1, "them": 0}


def test_before_the_window_it_is_upcoming_and_not_editable(tmp_path):
    client, tokens = make_client(tmp_path, now=KICKOFF - timedelta(days=2))
    body = client.get(f"/api/{tokens['staff']}/1").json()
    assert body["match"]["status"] == "upcoming" and not body["match"]["editable"]


def test_tokens_are_matched_case_insensitively(tmp_path):
    client, tokens = make_client(tmp_path)
    assert client.get(f"/api/{tokens['family'].upper()}").status_code == 200


@pytest.mark.parametrize(
    "path", ["/api/{f}/9", "/api/{f}/abc", "/api/{f}/1.5", "/api/{f}/%C2%B2"]
)
def test_unknown_matches_and_tokens_get_the_same_404(tmp_path, path):
    client, tokens = make_client(tmp_path)
    answer = client.get(path.format(f=tokens["family"]))
    assert answer.status_code == 404
    assert answer.json() == {"error": "not_found", "detail": "not found"}
    assert client.get("/api/zzzzzz/1").json() == answer.json()
    assert client.get("/api/zzzzzz").json() == answer.json()


def test_pages_are_html_for_a_known_token_and_match(tmp_path):
    client, tokens = make_client(tmp_path)
    for token in tokens.values():
        listing = client.get(f"/{token}")
        match = client.get(f"/{token}/1")
        assert listing.status_code == match.status_code == 200
        assert listing.headers["content-type"].startswith("text/html")
        assert "<title>Match</title>" in match.text
    assert client.get("/zzzzzz").status_code == 404
    assert client.get(f"/{tokens['family']}/9").status_code == 404


def test_scripts_and_pages_are_revalidated_at_every_use(tmp_path):
    client, tokens = make_client(tmp_path)
    script = client.get("/static/family.js")
    assert script.headers["cache-control"] == "no-cache"
    assert script.headers["etag"]
    # the second visit costs a 304, not the file
    again = client.get(
        "/static/family.js", headers={"If-None-Match": script.headers["etag"]}
    )
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"
    for path in (f"/{tokens['family']}", f"/{tokens['staff']}/1"):
        assert client.get(path).headers["cache-control"] == "no-cache"


def test_static_files_and_no_generated_docs(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/static/list.html").status_code == 200
    assert client.get("/docs").status_code == 404
    assert client.get("/static/nothing.js").status_code == 404
