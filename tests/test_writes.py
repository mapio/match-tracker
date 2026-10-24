import logging
from datetime import timedelta

import pytest
from test_api import KICKOFF, make_client

from match_tracker.store import Store


@pytest.fixture
def match(tmp_path):
    """A staff client, with the family token, for match 1, mid-window."""
    client, tokens = make_client(tmp_path)
    return client, tokens["staff"], tokens["family"]


def post(client, token, **body):
    return client.post(f"/api/{token}/1/events", json=body)


def start(client, token, set_=1):
    return post(client, token, set=set_, type="set_start")


def stored(tmp_path):
    return Store(tmp_path / "data").get("rho", 1)


def test_a_set_at_deuce_closes_only_with_a_margin_of_two(match):
    client, staff, _ = match
    start(client, staff)
    for _ in range(24):
        post(client, staff, set=1, type="point", team="us")
        post(client, staff, set=1, type="point", team="them")
    open_ = post(client, staff, set=1, type="point", team="us").json()
    assert open_["sets"][0]["score"] == {"us": 25, "them": 24}
    assert not open_["sets"][0]["closed"]
    closed = post(client, staff, set=1, type="point", team="us").json()
    assert closed["sets"][0]["closed"] and closed["sets"][0]["winner"] == "us"
    after = post(client, staff, set=1, type="point", team="us")
    refused(after, 409, "set_closed")


def test_a_winning_point_adds_a_set_end_with_the_same_time(match, tmp_path):
    client, staff, _ = match
    start(client, staff)
    for _ in range(25):
        answer = post(client, staff, set=1, type="point", team="us")
    body = answer.json()
    assert answer.status_code == 201
    assert [e["type"] for e in body["events"][-2:]] == ["point", "set_end"]
    assert body["events"][-1]["t"] == body["events"][-2]["t"]
    assert body["sets"][0]["winner"] == "us" and body["current"] == 2
    assert [e["id"] for e in body["events"]] == list(range(1, 28))
    assert stored(tmp_path).next_id == 28


def test_events_are_stamped_with_the_server_time_and_saved(match, tmp_path):
    client, staff, _ = match
    body = start(client, staff).json()
    assert body["events"] == [
        {
            "id": 1,
            "t": "2026-10-10T13:00:00.000Z",
            "set": 1,
            "type": "set_start",
        }
    ]
    post(client, staff, set=1, type="point", team="us")
    assert [e.team for e in stored(tmp_path).events] == [None, "us"]
    stored_json = (tmp_path / "data" / "rho" / "1.json").read_text()
    assert "player" not in stored_json


def test_a_restart_keeps_everything(match, tmp_path):
    client, staff, _ = match
    start(client, staff)
    post(client, staff, set=1, type="point", team="us")
    again, tokens = make_client(tmp_path)
    body = again.get(f"/api/{tokens['staff']}/1").json()
    assert body["sets"][0]["score"] == {"us": 1, "them": 0}
    assert (
        post(again, tokens["staff"], set=1, type="point", team="them").json()["events"][
            -1
        ]["id"]
        == 3
    )


def test_a_family_token_cannot_write_and_gets_the_usual_404(match):
    client, staff, family = match
    start(client, staff)
    calls = [
        client.post(f"/api/{family}/1/events", json={"set": 1, "type": "set_end"}),
        client.patch(f"/api/{family}/1/events/1", json={"team": "us"}),
        client.delete(f"/api/{family}/1/events/1"),
        client.post(f"/api/{family}/1/sets/1/reopen"),
    ]
    for answer in calls:
        assert answer.status_code == 404
        assert answer.json() == {"error": "not_found", "detail": "not found"}


def test_writes_to_an_unknown_match_or_token_are_404(match):
    client, staff, _ = match
    assert (
        client.post(
            f"/api/{staff}/9/events", json={"set": 1, "type": "set_start"}
        ).status_code
        == 404
    )
    assert client.delete("/api/zzzzzz/1/events/1").status_code == 404


def test_deleting_a_point_is_an_undo(match):
    client, staff, _ = match
    start(client, staff)
    post(client, staff, set=1, type="point", team="us")
    body = client.delete(f"/api/{staff}/1/events/2").json()
    assert body["sets"][0]["score"] == {"us": 0, "them": 0}
    assert [e["id"] for e in body["events"]] == [1]
    assert (
        post(client, staff, set=1, type="point", team="us").json()["events"][-1]["id"]
        == 3
    )


def test_a_scorer_can_no_longer_be_sent_with_a_point_or_a_fix(match):
    client, staff, _ = match
    start(client, staff)
    post(client, staff, set=1, type="point", team="us")
    refused(
        post(client, staff, set=1, type="point", team="us", player="anna"),
        422,
        "invalid_event",
    )
    refused(
        client.patch(f"/api/{staff}/1/events/2", json={"player": "anna"}),
        422,
        "invalid_event",
    )


def test_a_timeout_goes_to_the_other_team_unless_it_has_none_left(match):
    client, staff, _ = match
    start(client, staff)
    for team in ("us", "them", "them"):
        post(client, staff, set=1, type="timeout", team=team)
    url = f"/api/{staff}/1/events"
    refused = client.patch(f"{url}/2", json={"team": "them"})
    assert (refused.status_code, refused.json()["error"]) == (409, "timeout_limit")
    moved = client.patch(f"{url}/3", json={"team": "us"})
    assert moved.json()["sets"][0]["timeouts"] == {"us": 2, "them": 1}


def test_reopening_a_set_and_its_match_end(match):
    client, staff, _ = match
    start(client, staff)
    post(client, staff, set=1, type="point", team="us")
    ended = post(client, staff, set=1, type="match_end").json()
    assert ended["match"]["status"] == "over"
    assert [e["type"] for e in ended["events"][-2:]] == ["set_end", "match_end"]
    body = client.post(f"/api/{staff}/1/sets/1/reopen").json()
    assert body["match"]["status"] == "live" and not body["sets"][0]["closed"]
    assert [e["type"] for e in body["events"]] == ["set_start", "point"]


def refused(answer, status, code):
    assert (answer.status_code, answer.json()["error"]) == (status, code)
    assert "detail" in answer.json()


def test_every_rule_code_comes_with_its_status(match, tmp_path):
    client, staff, _ = match
    url = f"/api/{staff}/1"
    refused(post(client, staff, set=1, type="point", team="us"), 409, "set_not_started")
    start(client, staff)
    refused(start(client, staff), 409, "set_already_started")
    refused(start(client, staff, 2), 409, "previous_set_open")
    for _ in range(2):
        post(client, staff, set=1, type="timeout", team="us")
    refused(post(client, staff, set=1, type="timeout", team="us"), 409, "timeout_limit")
    refused(client.delete(f"{url}/events/1"), 409, "protected_event")
    post(client, staff, set=1, type="match_end")
    refused(post(client, staff, set=1, type="point", team="us"), 409, "match_over")
    client.post(f"{url}/sets/1/reopen")
    post(client, staff, set=1, type="set_end")
    refused(post(client, staff, set=1, type="point", team="us"), 409, "set_closed")


@pytest.mark.parametrize(
    "offset, code",
    [(timedelta(minutes=-90), "not_open_yet"), (timedelta(hours=24), "locked")],
)
def test_nothing_is_written_outside_the_window(tmp_path, offset, code):
    client, tokens = make_client(tmp_path, now=KICKOFF + offset)
    staff = tokens["staff"]
    refused(start(client, staff), 409, code)
    refused(client.delete(f"/api/{staff}/1/events/1"), 409, code)
    refused(client.post(f"/api/{staff}/1/sets/1/reopen"), 409, code)
    assert not (tmp_path / "data" / "rho" / "1.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"set": 1},
        {"type": "set_start"},
        {"set": "1", "type": "set_start"},
        {"set": True, "type": "set_start"},
        {"set": 1, "type": "set_start", "extra": 1},
        {"set": 1, "type": "flip"},
        {"set": 0, "type": "set_start"},
        {"set": 1, "type": "point"},
        {"set": 1, "type": "point", "team": "them", "player": "anna"},
        {"set": 1, "type": "point", "team": "us", "player": "anna"},
        {"set": 1, "type": "set_end", "team": "us"},
    ],
)
def test_a_malformed_event_is_422_and_changes_nothing(match, tmp_path, body):
    client, staff, _ = match
    answer = client.post(f"/api/{staff}/1/events", json=body)
    refused(answer, 422, "invalid_event")
    assert stored(tmp_path).events == ()


def test_malformed_fixes_are_422(match):
    client, staff, _ = match
    start(client, staff)
    post(client, staff, set=1, type="point", team="us")
    url = f"/api/{staff}/1/events"
    refused(client.patch(f"{url}/2", json={}), 422, "invalid_event")
    refused(
        client.patch(f"{url}/2", json={"player": "anna", "team": "us"}),
        422,
        "invalid_event",
    )
    refused(client.patch(f"{url}/2", json={"team": "us"}), 422, "invalid_event")
    refused(client.patch(f"{url}/abc", json={"team": "us"}), 422, "invalid_event")
    refused(client.patch(f"{url}/99", json={"team": "us"}), 422, "invalid_event")
    refused(client.patch(f"{url}/1", json={"team": "us"}), 422, "invalid_event")
    refused(client.delete(f"{url}/99"), 422, "invalid_event")
    refused(client.post(f"/api/{staff}/1/sets/2/reopen"), 422, "invalid_event")
    refused(client.post(f"/api/{staff}/1/sets/x/reopen"), 422, "invalid_event")


def test_each_applied_and_refused_write_is_logged(match, caplog):
    client, staff, _ = match
    with caplog.at_level(logging.INFO, logger="match-tracker"):
        start(client, staff)
        post(client, staff, set=1, type="point", team="us")
        post(client, staff, set=3, type="point", team="us")
        client.delete(f"/api/{staff}/1/events/2")
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "rho/1 set_start set 1",
        "rho/1 point us set 1",
        "rho/1 refused set_not_started",
        "rho/1 delete event 2",
    ]
