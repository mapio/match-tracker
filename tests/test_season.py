"""The coach's token, its start page, and the season: matches are prepared before
they are played."""

import copy
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_api import KICKOFF
from test_stream import serve
from test_teams import VALID

from match_tracker.server import create_app
from match_tracker.teams import ConfigError, Team, admin_token, coach_token

SALT = "s3cret"
BEFORE = KICKOFF - timedelta(days=3)  # long before match 1's window opens
OPEN = KICKOFF - timedelta(minutes=59)  # match 1's window is open, match 2's is not


def make(tmp_path, now=BEFORE, salt=SALT, **team):
    (tmp_path / "teams").mkdir(parents=True, exist_ok=True)
    raw = copy.deepcopy(VALID) | team
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(raw))
    app = create_app(tmp_path / "teams", tmp_path / "data", now=lambda: now, salt=salt)
    return TestClient(app), app.state.teams


def season(client, teams):
    return client.get(f"/api/{teams.coach_token('rho')}/season").json()["matches"]


def put(client, teams, items):
    token = teams.coach_token("rho")
    return client.put(f"/api/{token}/season", json={"matches": items})


def sent(match):
    """A match as the page sends it back: only what the season edits."""
    return {k: match[k] for k in ("n", "date", "opponent", "location")}


def saved_matches(tmp_path):
    return json.loads((tmp_path / "teams" / "rho.json").read_text())["matches"]


def log_of_match_1(tmp_path):
    folder = tmp_path / "data" / "rho"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "1.json").write_text(json.dumps({"next_id": 1, "events": []}))


def test_the_coach_and_admin_tokens_are_longer_and_need_a_salt():
    assert len(coach_token("rho", SALT)) == 10 and len(admin_token(SALT)) == 14
    alphabet = set("acdefhmnprtu")
    assert set(coach_token("rho", SALT)) <= alphabet
    assert set(admin_token(SALT)) <= alphabet
    assert coach_token("rho", SALT) != coach_token("tau", SALT)
    assert coach_token("rho", SALT) != coach_token("rho", "other")
    assert coach_token("rho", "") is None and admin_token("") is None


def test_the_coach_token_finds_its_team_and_nothing_else_does(tmp_path):
    client, teams = make(tmp_path)
    token = teams.coach_token("rho")
    assert client.get(f"/api/{token}").json()["team"]["id"] == "rho"
    assert client.get(f"/api/{token.upper()}").status_code == 200
    # the same paths with a staff, family or unknown token are not the coach's
    for other in (
        teams.tokens["rho"]["staff"],
        teams.tokens["rho"]["family"],
        "z" * 10,
    ):
        assert client.get(f"/api/{other}/season").status_code == 404
        assert client.get(f"/api/{other}/team").status_code == 404
    client, _ = make(tmp_path / "plain", salt="")
    assert client.get("/api/zzzzzzzzzz").status_code == 404  # no salt, no token


def test_unknown_coach_tokens_count_against_the_limit(tmp_path):
    client, _ = make(tmp_path)
    codes = [client.get(f"/api/zzzzzzzz{i:02d}").status_code for i in range(11)]
    assert codes == [404] * 10 + [429]


def test_the_start_page_lists_the_matches_by_date(tmp_path):
    client, teams = make(tmp_path)
    body = client.get(f"/api/{teams.coach_token('rho')}").json()
    assert [m["opponent"] for m in body["matches"]] == ["Opponent", "Other"]
    assert all(set(m) == {"n", "date", "opponent"} for m in body["matches"])


def test_the_season_lists_by_date_what_is_closed_and_what_has_a_record(tmp_path):
    log_of_match_1(tmp_path)
    client, teams = make(tmp_path, now=OPEN)
    first, second = season(client, teams)
    assert first["location"] == {"name": "Gym", "address": "Via Roma 1, Milano"}
    assert (first["closed"], first["has_log"]) == (True, False)  # no events yet
    assert (second["closed"], second["has_log"]) == (False, False)


def test_a_match_is_changed_added_and_removed_keeping_the_rules(tmp_path):
    client, teams = make(tmp_path)
    items = [sent(m) for m in season(client, teams)]
    items[0]["date"] = "2026-10-11T16:00"
    items[0]["opponent"] = "Renamed"
    items[0]["location"] = {"name": "New gym"}  # no address any more
    new = {
        "date": "2027-01-09T18:00",
        "opponent": "Brand new",
        "location": {"name": "Hall"},
    }
    answer = put(client, teams, [items[0], new])  # match 2 is left out: removed
    assert answer.status_code == 200
    saved = saved_matches(tmp_path)
    assert [m["n"] for m in saved] == [1, 3]  # the new one takes the next number
    assert saved[0]["opponent"] == "Renamed"
    assert saved[0]["location"] == {"name": "New gym"}
    assert saved[1]["date"] == "2027-01-09T18:00" and "players" not in saved[1]
    assert [m["opponent"] for m in answer.json()["matches"]] == ["Renamed", "Brand new"]
    assert (tmp_path / "teams" / "rho.json.bak").exists()


def test_a_new_match_never_takes_a_number_that_was_used(tmp_path):
    client, teams = make(tmp_path)
    items = [sent(m) for m in season(client, teams)]
    new = {
        "date": "2027-01-09T18:00",
        "opponent": "Third",
        "location": {"name": "Hall"},
    }
    put(client, teams, [items[0], new])  # match 2 removed, 3 added
    again = put(client, teams, [*[sent(m) for m in season(client, teams)], new])
    assert [m["n"] for m in again.json()["matches"]] == [1, 3, 4]


@pytest.mark.parametrize(
    "change, text",
    [
        ({"date": "tomorrow"}, "date"),
        ({"opponent": " "}, "opponent"),
        ({"location": {"name": ""}}, "location.name"),
    ],
)
def test_a_bad_match_is_refused_with_the_validators_words_and_nothing_is_written(
    tmp_path, change, text
):
    client, teams = make(tmp_path)
    before = (tmp_path / "teams" / "rho.json").read_text()
    answer = put(client, teams, [{**sent(season(client, teams)[0]), **change}])
    assert answer.status_code == 422 and text in answer.json()["detail"]
    assert answer.json()["error"] == "invalid_event"
    assert (tmp_path / "teams" / "rho.json").read_text() == before


def test_a_match_that_is_open_for_scoring_cannot_be_changed(tmp_path):
    client, teams = make(tmp_path, now=OPEN)
    first, second = (sent(m) for m in season(client, teams))
    for field, value in (
        ("date", "2026-10-11T15:00"),
        ("opponent", "Someone else"),
        ("location", {"name": "Elsewhere"}),
    ):
        answer = put(client, teams, [{**first, field: value}, second])
        assert answer.status_code == 409 and answer.json()["error"] == "match_closed"
    assert saved_matches(tmp_path)[0]["opponent"] == "Opponent"
    # sent back as it is, with a change to a future one, it is fine
    second["opponent"] = "Changed"
    assert put(client, teams, [first, second]).status_code == 200
    assert saved_matches(tmp_path)[1]["opponent"] == "Changed"


def test_a_match_is_not_moved_to_a_time_already_open_nor_added_there(tmp_path):
    client, teams = make(tmp_path, now=KICKOFF - timedelta(days=1))
    first, second = (sent(m) for m in season(client, teams))
    second["date"] = "2026-10-09T15:00"  # in the past
    refused = put(client, teams, [first, second])
    assert refused.status_code == 409 and refused.json()["error"] == "match_closed"
    past = {"date": "2026-10-09T15:00", "opponent": "Old", "location": {"name": "X"}}
    assert (
        put(client, teams, [first, {**sent(second), "n": 2}, past]).status_code == 409
    )
    assert (
        put(client, teams, [first, sent(season(client, teams)[1])]).status_code == 200
    )


def test_a_match_with_a_record_cannot_be_removed_nor_changed(tmp_path):
    log_of_match_1(tmp_path)
    client, teams = make(tmp_path, now=OPEN)
    first, second = (sent(m) for m in season(client, teams))
    removed = put(client, teams, [second])
    assert removed.status_code == 422 and "has a log" in removed.json()["detail"]
    moved = put(client, teams, [{**first, "opponent": "Other name"}, second])
    assert moved.status_code == 409 and moved.json()["error"] == "match_closed"
    assert [m["has_log"] for m in season(client, teams)] == [False, False]
    # the log written by a real event shows as a record
    staff = teams.tokens["rho"]["staff"]
    body = {"set": 1, "type": "set_start", "team": "us"}
    assert client.post(f"/api/{staff}/1/events", json=body).status_code == 201
    assert season(client, teams)[0]["has_log"] is True


def test_a_played_or_open_match_without_a_record_can_be_removed(tmp_path):
    client, teams = make(tmp_path, now=OPEN)
    _, second = (sent(m) for m in season(client, teams))
    assert put(client, teams, [second]).status_code == 200  # nothing was recorded
    assert [m["n"] for m in saved_matches(tmp_path)] == [2]


def test_a_match_number_the_team_does_not_have_is_refused(tmp_path):
    client, teams = make(tmp_path)
    bad = {
        "n": 9,
        "date": "2027-01-09T18:00",
        "opponent": "X",
        "location": {"name": "Y"},
    }
    answer = put(client, teams, [*[sent(m) for m in season(client, teams)], bad])
    assert answer.status_code == 422 and "no match 9" in answer.json()["detail"]


def test_a_season_with_no_matches_can_be_filled(tmp_path):
    client, teams = make(tmp_path, matches=[])
    new = {
        "date": "2027-01-09T18:00",
        "opponent": "First",
        "location": {"name": "Hall"},
    }
    answer = put(client, teams, [new])
    assert answer.status_code == 200 and answer.json()["matches"][0]["n"] == 1


def test_a_team_file_cannot_carry_the_old_season_switch():
    with pytest.raises(ConfigError, match="season_editing: unknown key"):
        Team.parse("rho", {**VALID, "season_editing": True})


def test_the_pages_are_served_to_the_coach_only(tmp_path):
    client, teams = make(tmp_path)
    token = teams.coach_token("rho")
    for path in ("", "/team", "/season"):
        page = client.get(f"/{token}{path}")
        assert page.status_code == 200, path
    assert client.get(f"/{token}/1/players").status_code == 404  # no call-up page
    assert "coach.js" in client.get(f"/{token}").text
    assert "season.js" in client.get(f"/{token}/season").text
    for path in ("", "/season"):
        assert client.get(f"/zzzzzzzzzz{path}").status_code == 404


def test_the_links_page_has_a_coach_column_and_the_admin_only_with_a_salt(tmp_path):
    _, teams = make(tmp_path)
    page = teams.markdown("https://example.org")
    token = teams.coach_token("rho")
    assert "| Team | Family | Staff | Coach |" in page
    assert f"[/{token}](https://example.org/{token})" in page
    admin = f"/{teams.admin_token}"
    assert f"Admin: [{admin}](https://example.org{admin})" in page
    _, plain = make(tmp_path / "plain", salt="")
    text = plain.markdown("https://example.org")
    assert "Coach" not in text and "Admin" not in text


@pytest.fixture
def live(tmp_path):
    with serve(tmp_path, salt=SALT) as live:
        live.clock[0] = BEFORE
        yield live


def season_page(live):
    return f"{live.base}/{live.teams.coach_token('rho')}/season"


def test_the_season_is_edited_on_the_page(tmp_path, page, live):
    page.goto(season_page(live))
    page.wait_for_selector(".card")
    assert page.locator(".card").count() == 2
    page.locator(".card input[type=text]").nth(0).fill("Changed opponent")
    page.click("button.add")
    new = page.locator(".card").nth(2)
    new.locator("input[type=datetime-local]").fill("2027-01-09T18:00")
    new.locator("input[type=text]").nth(0).fill("Brand new")
    new.locator("input[type=text]").nth(1).fill("Hall")
    page.click("button.save")
    page.wait_for_selector(".notice:has-text('Salvato')")
    saved = json.loads(live.team_file.read_text())["matches"]
    assert [m["opponent"] for m in saved] == ["Changed opponent", "Other", "Brand new"]
    assert saved[2]["n"] == 3 and saved[2]["location"] == {"name": "Hall"}
    page.locator(".card").nth(1).locator("button.remove").click()
    page.click("button.save")
    page.wait_for_selector(".notice:has-text('Salvato')")
    assert page.locator(".card").count() == 2
    names = [m["opponent"] for m in json.loads(live.team_file.read_text())["matches"]]
    assert names == ["Changed opponent", "Brand new"]
    assert page.errors == []


def test_an_open_match_is_read_only_on_the_page_and_a_bad_date_says_why(
    tmp_path, page, live
):
    live.clock[0] = OPEN
    page.goto(season_page(live))
    page.wait_for_selector(".card")
    first, second = page.locator(".card").all()
    assert all(i.is_disabled() for i in first.locator("input").all())
    assert "scoring" in first.inner_text() or "punteggio" in first.inner_text()
    assert not any(i.is_disabled() for i in second.locator("input").all())
    page.click("button.add")
    page.click("button.save")  # the new match is empty
    page.wait_for_selector(".notice.error")
    assert "matches[2]" in page.inner_text(".notice.error")
    assert page.errors == []


def test_the_editing_pages_use_the_width_of_a_tablet(tmp_path, page, live):
    page.set_viewport_size({"width": 1024, "height": 768})
    coach = live.teams.coach_token("rho")
    page.goto(f"{live.base}/{coach}/season")
    page.wait_for_selector(".card")
    first, second = (card.bounding_box() for card in page.locator(".card").all())
    assert first["y"] == second["y"] and first["x"] < second["x"]  # side by side
    assert page.locator("#app").bounding_box()["width"] > 700
    page.goto(f"{live.base}/{coach}/team")
    page.wait_for_selector("section")
    xs = {
        round(s.bounding_box()["x"]) for s in page.locator(".columns > section").all()
    }
    assert len(xs) == 2  # the team and its rules, side by side
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    # add and save are one size
    page.goto(f"{live.base}/{coach}/season")
    page.wait_for_selector("button.add")
    add, save = (page.locator(f"button.{k}").bounding_box() for k in ("add", "save"))
    assert (add["width"], add["height"]) == (save["width"], save["height"])
    assert page.errors == []
