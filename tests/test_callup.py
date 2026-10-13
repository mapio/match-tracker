"""The players called up for a match, changed by the coach with the team's coach
token; the staff token, which only scores, cannot."""

import json
from datetime import timedelta

import pytest
from test_api import KICKOFF
from test_season import SALT, make
from test_stream import serve

EARLY = KICKOFF - timedelta(hours=2)  # before the call-up closes
CLOSES = KICKOFF - timedelta(minutes=30)


def url(teams, suffix="callup", n=1):
    return f"/api/{teams.coach_token('rho')}/{n}/{suffix}"


def put(client, teams, players):
    return client.put(url(teams), json={"players": players})


def test_the_coach_sees_the_roster_and_who_is_called_up(tmp_path):
    client, teams = make(tmp_path, now=EARLY)
    body = client.get(url(teams)).json()
    assert body["roster"] == {"anna": "Anna", "bea": "Bea", "chiara": "Chiara"}
    assert body["players"] == ["anna", "bea"] and body["open"] is True
    assert body["closes_at"] == "2026-10-10T12:30:00.000Z"
    assert body["match"]["opponent"] == "Opponent" and body["team"]["lang"] == "it"
    assert client.get(url(teams, n=9)).status_code == 404


def test_the_staff_and_the_family_cannot_see_or_change_it(tmp_path):
    client, teams = make(tmp_path, now=EARLY)
    for role in ("staff", "family"):
        token = teams.tokens["rho"][role]
        assert client.get(f"/api/{token}/1/callup").status_code == 404
        answer = client.put(f"/api/{token}/1/callup", json={"players": ["anna"]})
        assert answer.status_code == 404
        assert client.get(f"/api/{token}/1/callup").status_code == 404
        assert client.get(f"/{token}/1/players").status_code == 404
    saved = json.loads((tmp_path / "teams" / "rho.json").read_text())
    assert saved["matches"][0]["players"] == ["anna", "bea"]


def test_a_change_is_saved_in_roster_order_with_a_backup_and_shows_everywhere(tmp_path):
    client, teams = make(tmp_path, now=EARLY)
    answer = put(client, teams, ["chiara", "anna"])
    assert answer.status_code == 200 and answer.json()["players"] == ["anna", "chiara"]
    saved = json.loads((tmp_path / "teams" / "rho.json").read_text())
    assert saved["matches"][0]["players"] == ["anna", "chiara"]
    assert saved["matches"][1]["players"] != []  # the other matches are untouched
    assert (tmp_path / "teams" / "rho.json.bak").exists()
    for role in ("staff", "family"):
        state = client.get(f"/api/{teams.tokens['rho'][role]}/1").json()
        assert list(state["players"]) == ["anna", "chiara"]


def test_the_call_up_is_closed_from_30_minutes_before_the_start(tmp_path):
    client, teams = make(tmp_path, now=CLOSES - timedelta(seconds=1))
    assert put(client, teams, ["anna"]).status_code == 200
    client, teams = make(tmp_path / "late", now=CLOSES)
    answer = put(client, teams, ["anna"])
    assert answer.status_code == 409 and answer.json()["error"] == "callup_closed"
    assert client.get(url(teams)).json()["open"] is False
    saved = json.loads((tmp_path / "late" / "teams" / "rho.json").read_text())
    assert saved["matches"][0]["players"] == ["anna", "bea"]


def test_an_unknown_player_and_a_malformed_body_are_refused(tmp_path):
    client, teams = make(tmp_path, now=EARLY)
    bad = put(client, teams, ["anna", "zoe"])
    assert bad.status_code == 422 and bad.json()["error"] == "unknown_player"
    for body in ({}, {"players": "anna"}, {"players": ["anna"], "extra": 1}):
        answer = client.put(url(teams), json=body)
        assert answer.status_code == 422 and answer.json()["error"] == "invalid_event"
    saved = json.loads((tmp_path / "teams" / "rho.json").read_text())
    assert saved["matches"][0]["players"] == ["anna", "bea"]


def test_a_player_who_has_played_cannot_be_taken_off(tmp_path):
    client, teams = make(tmp_path, now=KICKOFF - timedelta(minutes=45))
    staff = teams.tokens["rho"]["staff"]
    events = f"/api/{staff}/1/events"
    assert client.post(events, json={"set": 1, "type": "set_start"}).status_code == 201
    point = {"set": 1, "type": "point", "team": "us", "player": "anna"}
    assert client.post(events, json=point).status_code == 201
    answer = put(client, teams, ["bea"])
    assert answer.status_code == 409 and answer.json()["error"] == "player_in_use"
    assert put(client, teams, ["anna", "chiara"]).status_code == 200


def test_the_staff_list_has_no_call_up_in_it(tmp_path):
    client, teams = make(tmp_path, now=EARLY)
    staff = client.get(f"/api/{teams.tokens['rho']['staff']}").json()["matches"]
    assert all("callup_open" not in m for m in staff)


def test_the_page_is_served_to_the_coach_only(tmp_path):
    client, teams = make(tmp_path, now=EARLY)
    page = client.get(f"/{teams.coach_token('rho')}/1/players")
    assert page.status_code == 200 and "callup.js" in page.text
    assert client.get(f"/{teams.coach_token('rho')}/9/players").status_code == 404


@pytest.fixture
def live_early(tmp_path):
    with serve(tmp_path, salt=SALT) as live:
        live.clock[0] = EARLY
        yield live


def test_a_call_up_is_made_on_the_page(tmp_path, page, live_early):
    live = live_early
    coach = live.teams.coach_token("rho")
    page.goto(f"{live.base}/{coach}")
    page.wait_for_selector(".card")
    page.click("a.callup")  # the first match's
    page.wait_for_selector(".pick")
    assert page.locator(".pick.on").all_inner_texts() == ["Anna", "Bea"]
    page.click(".pick:has-text('Chiara')")
    page.click(".pick:has-text('Bea')")
    page.click("button.save")
    page.wait_for_selector(".notice:has-text('Salvato')")
    saved = json.loads(live.team_file.read_text())
    assert saved["matches"][0]["players"] == ["anna", "chiara"]
    assert page.locator(".pick.on").all_inner_texts() == ["Anna", "Chiara"]
    assert page.errors == []


def test_a_closed_call_up_cannot_be_changed_on_the_page(tmp_path, page, live_early):
    live = live_early
    page.goto(f"{live.base}/{live.teams.coach_token('rho')}/1/players")
    page.wait_for_selector(".pick")
    live.clock[0] = CLOSES  # it closes while the page is open
    page.click(".pick:has-text('Chiara')")
    page.click("button.save")
    page.wait_for_selector(".notice.error:has-text('chiuse')")
    assert all(button.is_disabled() for button in page.locator(".pick").all())
    assert page.locator("button.save").count() == 0
    saved = json.loads(live.team_file.read_text())
    assert saved["matches"][0]["players"] == ["anna", "bea"]
    assert page.errors == []
