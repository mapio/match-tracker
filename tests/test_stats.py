"""The anonymous statistics: what is counted, how visitors are told apart without
a cookie, and that nothing identifying is written."""

import copy
import json
import stat
from datetime import timedelta

from fastapi.testclient import TestClient
from test_admin import info
from test_api import KICKOFF
from test_teams import VALID

from match_tracker import server
from match_tracker.server import create_app
from match_tracker.stats import Stats, oldest_kept, read

SALT = "pepper"


def make(tmp_path, clock=None):
    clock = clock if clock is not None else [KICKOFF]
    (tmp_path / "teams").mkdir(exist_ok=True)
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(copy.deepcopy(VALID)))
    app = create_app(
        tmp_path / "teams",
        tmp_path / "data",
        now=lambda: clock[0],
        salt=SALT,
        logs_dir=tmp_path / "logs",
    )
    teams = app.state.teams
    tokens = {
        **teams.tokens["rho"],
        "coach": teams.coach_token("rho"),
        "admin": teams.admin_token,
    }
    return TestClient(app), app, tokens, clock


def lines(tmp_path):
    out = []
    for path in sorted((tmp_path / "logs").glob("stats-*.jsonl")):
        out += [json.loads(line) for line in path.read_text().splitlines()]
    return out


def days_summary(tmp_path):
    visitors, hits, edits = read(tmp_path / "logs", KICKOFF.date() - timedelta(days=9))
    return visitors, hits, edits


def test_visitors_are_told_apart_by_address_and_user_agent_within_a_day(tmp_path):
    client, app, tokens, _ = make(tmp_path)
    for agent in ("phone", "phone", "tablet"):
        client.get(f"/{tokens['family']}/1", headers={"user-agent": agent})
    app.state.stats.flush()
    visitors, hits, _ = days_summary(tmp_path)
    assert visitors == {("2026-10-10", "rho", 1, "family"): 2}
    assert hits == {("2026-10-10", "rho", 1, "family:match"): 3}


def test_each_kind_of_page_is_named_by_who_opens_it(tmp_path):
    client, app, tokens, _ = make(tmp_path)
    for path in (
        f"/{tokens['family']}",
        f"/{tokens['staff']}",
        f"/{tokens['staff']}/1",
        f"/{tokens['coach']}",
        f"/{tokens['coach']}/season",
        f"/{tokens['coach']}/team",
        f"/{tokens['coach']}/1/players",
        f"/{tokens['admin']}",
        f"/{tokens['admin']}/new",
    ):
        assert client.get(path).status_code == 200
    app.state.stats.flush()
    _, hits, _ = days_summary(tmp_path)
    assert {(team, match, page) for (_, team, match, page) in hits} == {
        ("rho", None, "family:list"),
        ("rho", None, "staff:list"),
        ("rho", 1, "staff:match"),
        ("rho", None, "coach:dashboard"),
        ("rho", None, "coach:season"),
        ("rho", None, "coach:team"),
        ("rho", 1, "coach:callup"),
        (None, None, "admin:teams"),
        (None, None, "admin:new"),
    }


def test_unknown_tokens_the_api_the_stream_and_the_statics_are_not_counted(tmp_path):
    client, app, tokens, _ = make(tmp_path)
    for path in (
        "/zzzzzz",
        "/zzzzzz/1",
        f"/api/{tokens['family']}",
        f"/api/{tokens['family']}/1",
        "/static/common.js",
        "/privacy",
        "/favicon.ico",
        f"/{tokens['family']}/99",
    ):
        client.get(path)
    app.state.stats.flush()
    assert lines(tmp_path) == []


def test_an_edit_is_a_line_with_its_kind_and_no_name(tmp_path):
    client, _, tokens, _ = make(tmp_path)
    post = client.post
    api = f"/api/{tokens['staff']}/1/events"
    post(api, json={"set": 1, "type": "set_start"})
    post(api, json={"set": 1, "type": "point", "team": "us", "player": "anna"})
    post(
        api, json={"set": 1, "type": "point", "team": "us", "player": "zoe"}
    )  # refused
    _, _, edits = days_summary(tmp_path)
    assert edits == {
        ("2026-10-10", "rho", "staff", "set_start"): 1,
        ("2026-10-10", "rho", "staff", "point"): 1,
    }
    text = (tmp_path / "logs" / "stats-2026-10.jsonl").read_text()
    assert "anna" not in text and tokens["staff"] not in text


def test_the_coach_and_the_admin_edits_are_noted(tmp_path):
    clock = [KICKOFF - timedelta(days=3)]  # the season and the call-up are open
    client, _, tokens, _ = make(tmp_path, clock)
    coach = f"/api/{tokens['coach']}"
    matches = client.get(f"{coach}/season").json()["matches"]
    sent = [{k: m[k] for k in ("n", "date", "opponent", "location")} for m in matches]
    assert client.put(f"{coach}/season", json={"matches": sent}).status_code == 200
    assert client.put(f"{coach}/team", json=info()).status_code == 200
    assert (
        client.put(f"{coach}/1/callup", json={"players": ["anna"]}).status_code == 200
    )
    created = client.post(f"/api/{tokens['admin']}", json={**info(), "id": "tau"})
    assert created.status_code == 201
    _, _, edits = days_summary(tmp_path)
    assert edits == {
        ("2026-10-07", "rho", "coach", "season"): 1,
        ("2026-10-07", "rho", "coach", "team"): 1,
        ("2026-10-07", "rho", "coach", "callup"): 1,
        ("2026-10-07", "tau", "admin", "create"): 1,
    }


def test_nothing_that_identifies_is_written(tmp_path):
    client, app, tokens, _ = make(tmp_path)
    client.get(f"/{tokens['family']}/1", headers={"user-agent": "SecretBrowser/9"})
    client.post(
        f"/api/{tokens['staff']}/1/events", json={"set": 1, "type": "set_start"}
    )
    app.state.stats.flush()
    text = "".join(p.read_text() for p in (tmp_path / "logs").glob("stats-*"))
    for secret in (*tokens.values(), "testclient", "SecretBrowser", "127.0.0.1"):
        assert secret not in text


def test_the_next_day_starts_with_a_new_secret_so_the_visitor_is_counted_anew(tmp_path):
    client, app, tokens, clock = make(tmp_path)
    first = app.state.stats
    client.get(f"/{tokens['family']}/1")
    secret = first.secret
    clock[0] += timedelta(days=1)
    client.get(f"/{tokens['family']}/1")
    assert first.secret != secret
    first.flush()
    visitors, _, _ = days_summary(tmp_path)
    assert visitors == {
        ("2026-10-10", "rho", 1, "family"): 1,
        ("2026-10-11", "rho", 1, "family"): 1,
    }


def test_a_restart_in_the_day_adds_its_run_and_a_flush_does_not_repeat(tmp_path):
    clock = [KICKOFF]
    (tmp_path / "logs").mkdir()
    for _ in range(2):  # two runs of the server
        stats = Stats(tmp_path / "logs", lambda: clock[0])
        stats.visit("a", "ua", "family", "rho", 1, "match")
        stats.flush()
        stats.visit("a", "ua", "family", "rho", 1, "match")
        stats.flush()
        stats.flush()
    _, hits, _ = days_summary(tmp_path)
    assert hits == {("2026-10-10", "rho", 1, "family:match"): 4}


def test_statistics_older_than_24_months_are_deleted_at_start(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    for month in ("2024-09", "2024-10", "2026-10"):
        (logs / f"stats-{month}.jsonl").write_text("")
    Stats(logs, lambda: KICKOFF)
    assert sorted(p.name for p in logs.iterdir()) == [
        "stats-2024-10.jsonl",
        "stats-2026-10.jsonl",
    ]
    assert oldest_kept(KICKOFF.date()) == "2024-10"


def test_the_files_are_private(tmp_path):
    client, _, tokens, _ = make(tmp_path)
    client.get(f"/{tokens['family']}")
    path = tmp_path / "logs" / "stats-2026-10.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_command_prints_the_figures(tmp_path, capsys, monkeypatch):
    client, app, tokens, _ = make(tmp_path)
    client.get(f"/{tokens['family']}/1")
    client.post(
        f"/api/{tokens['staff']}/1/events", json={"set": 1, "type": "set_start"}
    )
    app.state.stats.flush()
    monkeypatch.setattr(server, "datetime", _FixedDatetime)
    server.main(["stats", "--logs", str(tmp_path / "logs")])
    out = capsys.readouterr().out
    assert "2026-10-10  rho" in out and "family" in out
    assert "family:match" in out and "set_start" in out


class _FixedDatetime:
    @staticmethod
    def now(zone=None):
        return KICKOFF.astimezone(zone)
