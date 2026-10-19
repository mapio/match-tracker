"""The admin, one token for the whole server, creates teams and hands out the
links; the coach of a team then sets it up (name, colors, rules, roster)."""

import copy
import json

import pytest
from test_season import SALT, make
from test_stream import serve
from test_teams import VALID

INFO = {
    key: copy.deepcopy(VALID[key])
    for key in ("name", "lang", "timezone", "colors", "show_scorers", "rules", "roster")
}


def admin(teams, suffix=""):
    return f"/api/{teams.admin_token}{suffix}"


def team_url(teams):
    return f"/api/{teams.coach_token('rho')}/team"


def info(**changes):
    return {**copy.deepcopy(INFO), **changes}


def file_of(tmp_path, name="rho"):
    return json.loads((tmp_path / "teams" / f"{name}.json").read_text())


def test_only_the_admin_token_opens_the_admin(tmp_path):
    client, teams = make(tmp_path)
    assert len(teams.admin_token) == 14
    assert client.get(admin(teams)).status_code == 200
    assert client.get(f"/api/{teams.admin_token.upper()}").status_code == 200
    assert "teams" in client.get(admin(teams)).json()
    # the same path with another token is that token's page, never the admin's
    for other in (teams.coach_token("rho"), teams.tokens["rho"]["staff"]):
        assert "teams" not in client.get(f"/api/{other}").json()
        assert client.post(
            f"/api/{other}", json={"id": "tau", **info()}
        ).status_code in (
            404,
            405,
            422,
        )
    assert client.get("/api/" + "z" * 14).status_code == 404
    assert sorted(p.name for p in (tmp_path / "teams").iterdir()) == ["rho.json"]
    client, _ = make(tmp_path / "plain", salt="")
    assert client.get("/api/" + "z" * 14).status_code == 404


def test_unknown_admin_tokens_count_against_the_limit(tmp_path):
    client, _ = make(tmp_path)
    codes = [client.get(f"/api/zzzzzzzzzzzz{i:02d}").status_code for i in range(11)]
    assert codes == [404] * 10 + [429]


def test_the_list_gives_every_team_its_links(tmp_path):
    client, teams = make(tmp_path)
    body = client.get(admin(teams)).json()
    assert body["languages"] == ["en", "it"]
    [team] = body["teams"]
    assert team["id"] == "rho" and team["name"] == "Volley Test"
    assert team["links"] == {
        "family": f"/{teams.tokens['rho']['family']}",
        "staff": f"/{teams.tokens['rho']['staff']}",
        "coach": f"/{teams.coach_token('rho')}",
    }


def test_a_team_is_created_with_its_links_and_the_others_keep_theirs(tmp_path):
    client, teams = make(tmp_path)
    before = copy.deepcopy(teams.tokens["rho"])
    answer = client.post(admin(teams), json={"id": "tau", **info(name="Tau")})
    assert answer.status_code == 201 and answer.json()["id"] == "tau"
    assert file_of(tmp_path, "tau")["matches"] == []
    assert teams.tokens["rho"] == before
    listed = {t["id"]: t for t in client.get(admin(teams)).json()["teams"]}
    assert set(listed) == {"rho", "tau"}
    assert listed["tau"]["links"]["coach"] == f"/{teams.coach_token('tau')}"
    staff = listed["tau"]["links"]["staff"]
    assert client.get(f"/api{staff}").json()["team"]["name"] == "Tau"
    assert client.get(f"/api{listed['tau']['links']['coach']}").status_code == 200


def test_a_team_id_that_is_taken_or_not_valid_is_refused(tmp_path):
    client, teams = make(tmp_path)
    taken = client.post(admin(teams), json={"id": "rho", **info()})
    assert taken.status_code == 422 and "already exists" in taken.json()["detail"]
    bad = client.post(admin(teams), json={"id": "Bad Id", **info()})
    assert bad.status_code == 422 and "team id" in bad.json()["detail"]
    assert sorted(p.name for p in (tmp_path / "teams").iterdir()) == ["rho.json"]


def test_the_admin_does_not_edit_a_team_the_coach_does(tmp_path):
    client, teams = make(tmp_path)
    assert client.get(admin(teams, "/rho")).status_code == 404
    assert client.put(admin(teams, "/rho"), json=info()).status_code in (404, 405)


def test_the_pages_are_served_to_the_admin_only(tmp_path):
    client, teams = make(tmp_path)
    for path in ("", "/new"):
        page = client.get(f"/{teams.admin_token}{path}")
        assert page.status_code == 200 and "admin.js" in page.text
    assert client.get("/" + "z" * 14).status_code == 404
    # a coach's token on the same path opens the coach's page, not the admin's
    coach = client.get(f"/{teams.coach_token('rho')}")
    assert coach.status_code == 200 and "coach.js" in coach.text
    assert client.get(f"/{teams.coach_token('rho')}/new").status_code == 404


def test_the_coach_sees_the_team_as_the_form_edits_it(tmp_path):
    client, teams = make(tmp_path, contact="https://wa.me/39")
    body = client.get(team_url(teams)).json()
    assert body["name"] == "Volley Test" and body["contact"] == "https://wa.me/39"
    assert body["languages"] == ["en", "it"] and "season_editing" not in body
    assert body["roster"] == VALID["roster"] and body["rules"] == VALID["rules"]
    for role in ("staff", "family"):
        token = teams.tokens["rho"][role]
        assert client.get(f"/api/{token}/team").status_code == 404


def test_a_change_keeps_the_matches_and_what_the_form_does_not_show(tmp_path):
    client, teams = make(tmp_path, demo=True)
    answer = client.put(
        team_url(teams),
        json=info(name="Renamed", colors={"us": "#000000", "them": "#ffffff"}),
    )
    assert answer.status_code == 200 and answer.json()["name"] == "Renamed"
    saved = file_of(tmp_path)
    assert saved["name"] == "Renamed" and saved["colors"]["us"] == "#000000"
    assert saved["demo"] is True and saved["matches"] == VALID["matches"]
    assert (tmp_path / "teams" / "rho.json.bak").exists()
    assert teams.team("rho").name == "Renamed"  # in force at once


def test_the_contact_is_set_and_cleared(tmp_path):
    client, teams = make(tmp_path)
    assert (
        client.put(team_url(teams), json=info(contact="https://wa.me/39")).status_code
        == 200
    )
    assert file_of(tmp_path)["contact"] == "https://wa.me/39"
    assert client.put(team_url(teams), json=info(contact="")).status_code == 200
    assert "contact" not in file_of(tmp_path)


def test_a_player_taken_off_the_roster_leaves_the_call_ups(tmp_path):
    client, teams = make(tmp_path)
    roster = {"anna": "Anna", "chiara": "Chiara"}  # bea goes
    assert client.put(team_url(teams), json=info(roster=roster)).status_code == 200
    saved = file_of(tmp_path)
    assert saved["matches"][0]["players"] == ["anna"]
    assert saved["roster"] == roster


def test_a_player_in_a_log_cannot_be_taken_off(tmp_path):
    client, teams = make(tmp_path)
    log = tmp_path / "data" / "rho"
    log.mkdir(parents=True)
    event = {
        "id": 1,
        "t": "2026-10-10T13:00:00.000Z",
        "set": 1,
        "type": "point",
        "team": "us",
        "player": "bea",
    }
    (log / "1.json").write_text(json.dumps({"next_id": 2, "events": [event]}))
    answer = client.put(team_url(teams), json=info(roster={"anna": "Anna"}))
    assert answer.status_code == 422 and "bea" in answer.json()["detail"]
    assert "bea" in file_of(tmp_path)["roster"]


@pytest.mark.parametrize(
    "change, text",
    [
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"roster": {}}, "roster"),
        ({"lang": "xx"}, "lang"),
        ({"rules": {**VALID["rules"], "best_of": 4}}, "best_of"),
        ({"contact": "not a url"}, "contact"),
    ],
)
def test_an_invalid_change_is_refused_with_the_validators_words(tmp_path, change, text):
    client, teams = make(tmp_path)
    before = (tmp_path / "teams" / "rho.json").read_text()
    answer = client.put(team_url(teams), json=info(**change))
    assert answer.status_code == 422 and text in answer.json()["detail"]
    assert (tmp_path / "teams" / "rho.json").read_text() == before


def test_the_form_takes_nothing_it_does_not_show(tmp_path):
    client, teams = make(tmp_path)
    answer = client.put(team_url(teams), json={**info(), "season_editing": True})
    assert answer.status_code == 422
    answer = client.put(team_url(teams), json={**info(), "matches": []})
    assert answer.status_code == 422 and file_of(tmp_path)["matches"] != []


@pytest.fixture
def live(tmp_path):
    with serve(tmp_path, salt=SALT) as live:
        yield live


def test_the_admin_lists_the_teams_and_creates_one_on_the_page(tmp_path, page, live):
    page.goto(f"{live.base}/{live.teams.admin_token}")
    page.wait_for_selector(".card")
    assert "Volley Test" in page.inner_text(".card")
    assert page.locator(".card .links a").count() == 3
    page.click("a.add")
    page.wait_for_selector("input[type=color]")
    page.locator("label:has-text('Identificativo') input").fill("tau")
    page.locator("label:has-text('Nome') input").first.fill("Tau team")
    page.click("button.add")
    page.locator(".player input").last.fill("Ada Östra")
    page.click("button.save")
    page.wait_for_selector(".card:has-text('Tau team')")
    created = json.loads((live.team_file.parent / "tau.json").read_text())
    assert created["name"] == "Tau team" and created["roster"] == {"1": "Ada Östra"}
    assert created["matches"] == [] and created["rules"]["best_of"] == 5
    assert page.errors == []


def test_a_bad_id_shows_the_reason_on_the_page(tmp_path, page, live):
    page.goto(f"{live.base}/{live.teams.admin_token}/new")
    page.wait_for_selector("input[type=color]")
    page.locator("label:has-text('Identificativo') input").fill("Bad Id")
    page.locator("label:has-text('Nome') input").first.fill("X")
    page.click("button.add")
    page.locator(".player input").last.fill("Ada")
    page.click("button.save")
    page.wait_for_selector(".notice.error:has-text('team id')")
    assert page.errors == []


def test_the_coach_changes_the_team_from_the_start_page(tmp_path, page, live):
    page.goto(f"{live.base}/{live.teams.coach_token('rho')}")
    page.wait_for_selector(".card")
    page.click("text=Impostazioni squadra")
    page.wait_for_selector("input[type=color]")
    page.locator("label:has-text('Nome') input").first.fill("Edited")
    page.click("button.add")
    page.locator(".player input").last.fill("Dora Östra")
    page.click("button.save")
    page.wait_for_selector(".notice:has-text('Salvato')")
    saved = json.loads(live.team_file.read_text())
    assert saved["name"] == "Edited" and saved["roster"]["1"] == "Dora Östra"
    page.locator("label:has-text('Fuso orario') input").fill("Mars/Olympus")
    page.click("button.save")
    page.wait_for_selector(".notice.error:has-text('timezone')")
    assert json.loads(live.team_file.read_text())["timezone"] == "Europe/Rome"
    assert page.errors == []
