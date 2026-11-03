"""The practice game (docs/contract.md, *Practice game*)."""

import copy
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_api import KICKOFF, make_client
from test_stream import Reader, serve
from test_teams import VALID

import match_tracker.server as server_module
from match_tracker.server import create_app
from match_tracker.store import Store

LONG_AFTER = datetime(2031, 3, 3, 3, 3, tzinfo=UTC)  # no match window is near


@pytest.fixture
def game(tmp_path):
    client, tokens = make_client(tmp_path)
    return client, tokens["staff"], tokens["family"]


def url(token, *rest):
    return "/".join(["/api", token, "practice", *rest])


def post(client, token, **body):
    return client.post(url(token, "events"), json=body)


def start(client, token, team="us"):
    return post(client, token, set=1, type="set_start", team=team)


def score(client, token, team, times):
    for _ in range(times):
        answer = post(client, token, set=1, type="point", team=team)
        assert answer.status_code == 201, answer.text
    return answer.json()


def test_an_untouched_game_is_upcoming_and_has_no_window(game):
    client, staff, _ = game
    answer = client.get(url(staff))
    assert answer.status_code == 200
    body = answer.json()
    assert body["role"] == "staff"
    assert body["match"] == {
        "n": None,
        "date": None,
        "opponent": None,
        "location": None,
        "status": "upcoming",
        "opens_at": None,
        "locks_at": None,
        "editable": True,
        "practice": True,
    }
    assert body["rules"] == {
        "best_of": 1,
        "points": 25,
        "last_set_points": 25,
        "win_by": 2,
        "timeouts_per_set": VALID["rules"]["timeouts_per_set"],
    }
    assert body["sets"] == [] and body["events"] == []


def test_scoring_to_25_closes_the_set_and_ends_the_game(game):
    client, staff, _ = game
    assert start(client, staff).status_code == 201
    score(client, staff, "them", 10)
    body = score(client, staff, "us", 25)
    assert [e["type"] for e in body["events"][-2:]] == ["point", "set_end"]
    assert body["sets"][0]["closed"] and body["sets"][0]["winner"] == "us"
    assert body["sets"][0]["score"] == {"us": 25, "them": 10}
    assert body["match"]["status"] == "over"
    assert body["sets_won"] == {"us": 1, "them": 0}
    after = post(client, staff, set=1, type="point", team="us")
    assert after.status_code == 409
    assert after.json()["error"] == "match_over"
    assert post(client, staff, set=2, type="set_start", team="us").status_code == 422


def test_the_game_is_live_after_the_first_event(game):
    client, staff, _ = game
    body = start(client, staff).json()
    assert body["match"]["status"] == "live"
    assert body["match"]["practice"] is True


def test_at_24_24_a_margin_of_two_is_needed(game):
    client, staff, _ = game
    start(client, staff)
    for _ in range(24):
        score(client, staff, "us", 1)
        score(client, staff, "them", 1)
    body = score(client, staff, "us", 1)
    assert body["sets"][0]["score"] == {"us": 25, "them": 24}
    assert not body["sets"][0]["closed"]
    body = score(client, staff, "us", 1)
    assert body["sets"][0]["closed"] and body["match"]["status"] == "over"


def test_timeouts_are_limited_per_set_by_the_team(game):
    client, staff, _ = game
    limit = VALID["rules"]["timeouts_per_set"]
    start(client, staff)
    for _ in range(limit):
        assert post(client, staff, set=1, type="timeout", team="us").status_code == 201
    over = post(client, staff, set=1, type="timeout", team="us")
    assert over.status_code == 409 and over.json()["error"] == "timeout_limit"
    assert post(client, staff, set=1, type="timeout", team="them").status_code == 201


def test_a_timeout_limit_follows_the_team_file(tmp_path):
    rules = copy.deepcopy(VALID["rules"]) | {"timeouts_per_set": 0}
    client, tokens = make_client(tmp_path, rules=rules)
    staff = tokens["staff"]
    start(client, staff)
    assert client.get(url(staff)).json()["rules"]["timeouts_per_set"] == 0
    refused = post(client, staff, set=1, type="timeout", team="us")
    assert refused.json()["error"] == "timeout_limit"


def test_undo_a_point_and_reopen_a_closed_set(game):
    client, staff, _ = game
    start(client, staff)
    body = score(client, staff, "us", 25)
    assert body["match"]["status"] == "over"
    point = next(e for e in reversed(body["events"]) if e["type"] == "point")
    deleted = client.delete(url(staff, "events", str(point["id"])))
    assert deleted.status_code == 200
    assert deleted.json()["sets"][0]["score"] == {"us": 24, "them": 0}
    reopened = client.post(url(staff, "sets", "1", "reopen"))
    assert reopened.status_code == 200
    state = reopened.json()
    assert not state["sets"][0]["closed"] and state["match"]["status"] == "live"
    assert client.post(url(staff, "sets", "1", "reopen")).status_code == 422


def test_a_timeout_can_be_given_to_the_other_team(game):
    client, staff, _ = game
    start(client, staff)
    body = post(client, staff, set=1, type="timeout", team="us").json()
    timeout = body["events"][-1]
    fixed = client.patch(
        url(staff, "events", str(timeout["id"])), json={"team": "them"}
    )
    assert fixed.status_code == 200
    assert fixed.json()["sets"][0]["timeouts"] == {"us": 0, "them": 1}


def test_reset_works_on_a_team_that_is_not_a_demo_and_deletes_the_log(game, tmp_path):
    client, staff, _ = game
    start(client, staff)
    score(client, staff, "us", 3)
    path = tmp_path / "data" / "rho" / "practice.json"
    assert path.exists()
    answer = client.post(url(staff, "reset"))
    assert answer.status_code == 200
    body = answer.json()
    assert body["events"] == [] and body["match"]["status"] == "upcoming"
    assert not path.exists()
    # the next game starts from nothing, ids included
    assert start(client, staff).json()["events"][0]["id"] == 1
    # whereas a match of the same team still cannot be reset
    assert client.post(f"/api/{staff}/1/reset").status_code == 404


def test_reset_after_a_finished_game_starts_the_next_one(game):
    client, staff, _ = game
    start(client, staff)
    score(client, staff, "us", 25)
    client.post(url(staff, "reset"))
    assert start(client, staff, team="them").status_code == 201


def test_the_game_works_outside_any_match_window(tmp_path):
    client, tokens = make_client(tmp_path, now=LONG_AFTER)
    staff = tokens["staff"]
    assert client.post(f"/api/{staff}/1/events", json={}).status_code == 422
    match = client.post(
        f"/api/{staff}/1/events", json={"set": 1, "type": "set_start", "team": "us"}
    )
    assert match.status_code == 409 and match.json()["error"] == "locked"
    body = start(client, staff).json()
    assert body["match"]["editable"] is True
    score(client, staff, "us", 2)
    state = client.get(url(staff)).json()
    assert state["match"]["editable"] is True
    assert state["match"]["status"] == "live"
    assert state["sets"][0]["closed"] is False


def test_the_game_works_before_any_match_window_too(tmp_path):
    early = datetime(2020, 1, 1, tzinfo=UTC)
    client, tokens = make_client(tmp_path, now=early)
    assert start(client, tokens["staff"]).status_code == 201


def test_a_game_survives_a_restart(tmp_path):
    client, tokens = make_client(tmp_path)
    staff = tokens["staff"]
    start(client, staff)
    score(client, staff, "us", 4)
    again = TestClient(
        create_app(tmp_path / "teams", tmp_path / "data", now=lambda: KICKOFF)
    )
    body = again.get(url(staff)).json()
    assert body["sets"][0]["score"] == {"us": 4, "them": 0}
    assert Store(tmp_path / "data").get("rho", "practice").next_id == 6


def test_the_server_still_starts_with_a_practice_log_beside_the_matches(tmp_path):
    client, tokens = make_client(tmp_path)
    start(client, tokens["staff"])
    # a restart validates every log beside the team file, this one included
    create_app(tmp_path / "teams", tmp_path / "data", now=lambda: KICKOFF)


def test_no_journal_is_written(game, tmp_path):
    client, staff, _ = game
    start(client, staff)
    score(client, staff, "us", 3)
    client.delete(url(staff, "events", "2"))
    client.post(url(staff, "reset"))
    start(client, staff)
    files = sorted(p.name for p in (tmp_path / "data" / "rho").iterdir())
    assert files == ["practice.json"]


def test_the_game_is_in_no_list_season_or_chart(game, tmp_path):
    client, staff, family = game
    start(client, staff)
    score(client, staff, "us", 25)
    for token in (staff, family):
        listing = client.get(f"/api/{token}").json()
        assert [m["n"] for m in listing["matches"]] == [1, 2]
        assert all(m["status"] == "upcoming" for m in listing["matches"])
    assert client.get(f"/{staff}/practice/chart").status_code == 404
    assert client.get(url(staff, "chart")).status_code == 404
    # the matches themselves are untouched
    assert client.get(f"/api/{staff}/1").json()["events"] == []


def test_the_page_is_the_matchs_for_staff(game):
    client, staff, _ = game
    answer = client.get(f"/{staff}/practice")
    assert answer.status_code == 200
    assert answer.text == client.get(f"/{staff}/1").text


def test_a_family_token_gets_404_on_every_practice_path(game):
    client, staff, family = game
    start(client, staff)
    paths = [
        ("get", url(family), None),
        ("get", url(family, "stream"), None),
        ("post", url(family, "events"), {"set": 1, "type": "point", "team": "us"}),
        ("patch", url(family, "events", "1"), {"team": "them"}),
        ("delete", url(family, "events", "1")),
        ("post", url(family, "sets", "1", "reopen"), None),
        ("post", url(family, "reset"), None),
        ("get", f"/{family}/practice", None),
        ("get", f"/{family}/practice/chart", None),
    ]
    for method, path, *body in paths:
        answer = getattr(client, method)(
            path, **({"json": body[0]} if body and body[0] else {})
        )
        assert answer.status_code == 404, (method, path)
    state = client.get(url(staff)).json()
    assert len(state["events"]) == 1  # nothing was done


def test_an_unknown_token_is_the_usual_404_and_counts_against_the_limit(game):
    client, _, _ = game
    codes = [client.get("/api/zzzzzz/practice").status_code for _ in range(12)]
    assert codes[:10] == [404] * 10 and codes[10:] == [429, 429]


def test_a_bad_match_number_is_still_a_404(game):
    client, staff, _ = game
    for n in ("0", "99", "practic", "Practice1", "-1"):
        assert client.get(f"/api/{staff}/{n}").status_code == 404
        assert client.get(f"/{staff}/{n}").status_code == 404
    # the word is matched as written, not as a prefix
    assert client.get(f"/api/{staff}/practice/nonsense").status_code == 404


def test_coach_and_admin_tokens_get_404_on_the_practice_game(tmp_path):
    (tmp_path / "teams").mkdir()
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(VALID))
    (tmp_path / "teams" / "salt").write_text("pepper\n")
    app = create_app(
        tmp_path / "teams", tmp_path / "data", now=lambda: KICKOFF, salt="pepper"
    )
    client = TestClient(app)
    teams = app.state.teams
    assert client.get(url(teams.tokens["rho"]["staff"])).status_code == 200
    for token in (teams.coach_token("rho"), teams.admin_token):
        for path in (url(token), url(token, "stream"), f"/{token}/practice"):
            assert client.get(path).status_code in (404, 429), path
        assert client.post(url(token, "reset")).status_code in (404, 429)
    assert not (tmp_path / "data" / "rho" / "practice.json").exists()


def test_a_practice_log_with_a_player_is_refused_at_startup(tmp_path):
    make_client(tmp_path)
    folder = tmp_path / "data" / "rho"
    folder.mkdir(parents=True)
    bad = {
        "next_id": 2,
        "events": [{"id": 1, "t": "x", "set": 1, "type": "point", "player": "A"}],
    }
    (folder / "practice.json").write_text(json.dumps(bad))
    with pytest.raises(server_module.ConfigError):
        create_app(tmp_path / "teams", tmp_path / "data", now=lambda: KICKOFF)


def test_a_stream_sends_the_state_and_follows_the_scoring(tmp_path):
    import httpx

    with serve(tmp_path) as live:
        token = live.tokens["staff"]
        base = f"{live.base}/api/{token}/practice"
        with httpx.stream("GET", f"{base}/stream", timeout=5) as response:
            assert response.status_code == 200
            reader = Reader(response)
            kind, first = reader.next()
            assert kind == "state" and first["match"]["practice"] is True
            assert first["events"] == []
            answer = httpx.post(
                f"{base}/events",
                json={"set": 1, "type": "set_start", "team": "us"},
                timeout=5,
            )
            assert answer.status_code == 201
            state = reader.next_state(lambda s: len(s["events"]) == 1)
            assert state["match"]["status"] == "live"
        family = f"{live.base}/api/{live.tokens['family']}/practice/stream"
        assert httpx.get(family, timeout=5).status_code == 404
