"""The journal (journal.py): every change of a match is in its journal before the
state shows it, so the journal replays to the same log, and the saves of a team
file keep what the file had before."""

import json
import stat
from datetime import timedelta

from test_api import KICKOFF, make_client
from test_season import make

from match_tracker import journal, server


def state_file(tmp_path):
    return json.loads((tmp_path / "data" / "rho" / "1.json").read_text())


def journal_file(tmp_path):
    return journal.path_of(tmp_path / "data", "rho", 1)


def test_the_journal_replays_to_the_log_after_every_change(tmp_path):
    client, tokens = make_client(tmp_path)
    staff = tokens["staff"]
    base = f"/api/{staff}/1"
    steps = [
        ("post", "/events", {"set": 1, "type": "set_start", "team": "us"}),
        ("patch", "/events/1", {"team": "them"}),  # the first server was wrong
        ("post", "/events", {"set": 1, "type": "point", "team": "us"}),
        ("post", "/events", {"set": 1, "type": "point", "team": "them"}),
        ("post", "/events", {"set": 1, "type": "timeout", "team": "us"}),
        ("patch", "/events/4", {"team": "them"}),
        ("delete", "/events/3", None),
        ("post", "/events", {"set": 1, "type": "point", "team": "us"}),
        ("post", "/events", {"set": 1, "type": "set_end"}),
        ("post", "/sets/1/reopen", None),
        ("post", "/events", {"set": 1, "type": "point", "team": "them"}),
    ]
    for method, path, body in steps:
        answer = getattr(client, method)(
            base + path, **({"json": body} if body else {})
        )
        assert answer.status_code in (200, 201), (path, answer.text)
        assert journal.replay(journal_file(tmp_path)) == state_file(tmp_path), path
    lines = journal_file(tmp_path).read_text().splitlines()
    assert len(lines) == len(steps)


def test_a_deleted_event_and_a_fix_are_kept_whole(tmp_path):
    client, tokens = make_client(tmp_path)
    base = f"/api/{tokens['staff']}/1"
    client.post(f"{base}/events", json={"set": 1, "type": "set_start", "team": "us"})
    client.post(f"{base}/events", json={"set": 1, "type": "timeout", "team": "us"})
    client.patch(f"{base}/events/2", json={"team": "them"})
    client.delete(f"{base}/events/2")
    records = [json.loads(x) for x in journal_file(tmp_path).read_text().splitlines()]
    fix, drop = records[2], records[3]
    assert fix["who"] == "staff" and fix["op"] == "fix"
    assert fix["changed"][0]["before"]["team"] == "us"
    assert fix["changed"][0]["after"]["team"] == "them"
    assert drop["op"] == "delete" and drop["removed"][0]["team"] == "them"
    assert (
        state_file(tmp_path)["events"]
        == journal.replay(journal_file(tmp_path))["events"]
    )


def test_the_journal_is_private(tmp_path):
    client, tokens = make_client(tmp_path)
    base = f"/api/{tokens['staff']}/1"
    client.post(f"{base}/events", json={"set": 1, "type": "set_start", "team": "us"})
    client.post(f"{base}/events", json={"set": 1, "type": "point", "team": "us"})
    path = journal_file(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "player" not in path.read_text()


def test_the_team_journal_keeps_the_file_as_it_was(tmp_path):
    client, teams = make(tmp_path, now=KICKOFF - timedelta(days=3))
    before = json.loads((tmp_path / "teams" / "rho.json").read_text())
    items = [
        {k: m[k] for k in ("n", "date", "opponent", "location")}
        for m in before["matches"]
    ]
    items[0]["opponent"] = "Renamed"
    answer = client.put(
        f"/api/{teams.coach_token('rho')}/season", json={"matches": items}
    )
    assert answer.status_code == 200, answer.text
    path = journal.path_of(tmp_path / "data", "rho", journal.TEAM)
    (record,) = [json.loads(x) for x in path.read_text().splitlines()]
    assert record["who"] == "coach" and record["op"] == "season"
    assert record["before"] == before
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_journal_command_prints_the_log(tmp_path, capsys):
    client, tokens = make_client(tmp_path)
    base = f"/api/{tokens['staff']}/1"
    client.post(f"{base}/events", json={"set": 1, "type": "set_start", "team": "us"})
    server.main(["replay", str(journal_file(tmp_path))])
    assert json.loads(capsys.readouterr().out) == state_file(tmp_path)


def test_the_time_of_a_line_is_the_clock_of_the_app(tmp_path):
    later = KICKOFF + timedelta(minutes=5)
    client, tokens = make_client(tmp_path, now=later)
    client.post(
        f"/api/{tokens['staff']}/1/events",
        json={"set": 1, "type": "set_start", "team": "us"},
    )
    record = json.loads(journal_file(tmp_path).read_text())
    assert record["t"] == journal.stamp(later)
