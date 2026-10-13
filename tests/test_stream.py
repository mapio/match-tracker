"""The stream against a real server: Starlette's test client cannot read an endless
response, and the parts worth testing (several watchers, a client that leaves,
time passing) only exist over real connections."""

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

import httpx
import uvicorn
from test_api import KICKOFF
from test_teams import VALID

from match_tracker.server import create_app

WAIT = 5  # seconds before a test gives up on an event


class Reader:
    def __init__(self, response):
        self._lines = response.iter_lines()

    def next(self):
        """The next item: ('state', dict) or ('keepalive', None)."""
        event = data = None
        for line in self._lines:
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1])
            elif line.startswith(":"):
                return "keepalive", None
            elif line == "" and event:
                return event, data
        raise AssertionError("the stream ended")

    def next_state(self, until=lambda state: True):
        """The next state satisfying `until`, skipping keepalives."""
        deadline = time.time() + WAIT
        while time.time() < deadline:
            kind, data = self.next()
            if kind == "state" and until(data):
                return data
        raise AssertionError("no such state arrived")


class Live:
    def __init__(self, base, tokens, team_file, clock, teams=None):
        self.base, self.tokens, self.team_file, self.clock = (
            base,
            tokens,
            team_file,
            clock,
        )
        self.teams = teams  # the server's Teams, for tokens that are not in `tokens`

    @contextmanager
    def watch(self, role):
        url = f"{self.base}/api/{self.tokens[role]}/1/stream"
        with httpx.stream("GET", url, timeout=WAIT) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            yield Reader(response)

    def post(self, **body):
        url = f"{self.base}/api/{self.tokens['staff']}/1/events"
        answer = httpx.post(url, json=body, timeout=WAIT)
        assert answer.status_code == 201, answer.text
        return answer.json()


@contextmanager
def serve(tmp_path, keepalive=0.3, salt=""):
    (tmp_path / "teams").mkdir(exist_ok=True)
    team_file = tmp_path / "teams" / "rho.json"
    team_file.write_text(json.dumps(VALID))
    clock = [KICKOFF]
    app = create_app(
        tmp_path / "teams",
        tmp_path / "data",
        now=lambda: clock[0],
        keepalive=keepalive,
        salt=salt,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            timeout_graceful_shutdown=1,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + WAIT
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    assert server.started
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield Live(
            f"http://127.0.0.1:{port}",
            app.state.teams.tokens["rho"],
            team_file,
            clock,
            app.state.teams,
        )
    finally:
        server.should_exit = True
        thread.join(WAIT)


def test_the_first_event_is_the_state_shaped_for_the_role(tmp_path):
    with (
        serve(tmp_path) as live,
        live.watch("family") as family,
        live.watch("staff") as staff,
    ):
        seen_by_family = family.next()
        seen_by_staff = staff.next()
    assert seen_by_family[0] == seen_by_staff[0] == "state"
    assert seen_by_family[1]["role"] == "family" and "events" not in seen_by_family[1]
    assert seen_by_staff[1]["role"] == "staff" and seen_by_staff[1]["events"] == []


def test_a_write_reaches_every_watcher_of_the_match(tmp_path):
    with (
        serve(tmp_path) as live,
        live.watch("family") as one,
        live.watch("family") as two,
        live.watch("staff") as staff,
    ):
        for reader in (one, two, staff):
            reader.next_state()
        live.post(set=1, type="set_start")
        live.post(set=1, type="point", team="us", player="anna")
        states = [
            reader.next_state(lambda s: s["sets"] and s["sets"][0]["score"]["us"] == 1)
            for reader in (one, two, staff)
        ]
        for state in states:
            assert state["sets"][0]["score"] == {"us": 1, "them": 0}
        assert [f["player"] for f in states[0]["feed"]] == ["anna"]
        assert "feed" not in states[2] and states[2]["events"][-1]["id"] == 2


def test_quick_writes_end_with_the_latest_state(tmp_path):
    with serve(tmp_path) as live, live.watch("family") as watcher:
        watcher.next_state()
        live.post(set=1, type="set_start")
        for _ in range(8):
            live.post(set=1, type="point", team="them")
        state = watcher.next_state(
            lambda s: s["sets"] and s["sets"][0]["score"]["them"] == 8
        )
    assert state["sets"][0]["score"]["them"] == 8


def test_an_idle_stream_sends_keepalive_comments(tmp_path):
    with serve(tmp_path) as live, live.watch("family") as watcher:
        assert watcher.next()[0] == "state"
        assert watcher.next() == ("keepalive", None)


def test_time_alone_changes_the_state_sent(tmp_path):
    with serve(tmp_path) as live, live.watch("family") as watcher:
        live.post(set=1, type="set_start")
        before = watcher.next_state(lambda s: s["match"]["status"] == "live")
        assert before["match"]["editable"]
        live.clock[0] = KICKOFF + timedelta(hours=25)
        after = watcher.next_state(lambda s: s["match"]["status"] == "over")
    assert not after["match"]["editable"] and after["sets"][0]["closed"]


def test_a_team_file_change_reaches_the_watchers(tmp_path):
    with serve(tmp_path) as live, live.watch("family") as watcher:
        assert watcher.next_state()["team"]["name"] == "Volley Test"
        raw = json.loads(live.team_file.read_text())
        live.team_file.write_text(json.dumps(raw | {"name": "Renamed"}))
        stat = live.team_file.stat()  # a later modification time, whatever the clock
        os.utime(
            live.team_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000)
        )
        assert watcher.next_state(lambda s: s["team"]["name"] == "Renamed")


def test_a_watcher_that_leaves_is_forgotten_and_the_others_carry_on(tmp_path, caplog):
    with (
        caplog.at_level(logging.INFO, logger="match-tracker"),
        serve(tmp_path) as live,
        live.watch("family") as stays,
    ):
        with live.watch("family") as leaves:
            leaves.next_state()
            stays.next_state()
        deadline = time.time() + WAIT
        while time.time() < deadline and not any(
            "watcher left (1)" in r.getMessage() for r in caplog.records
        ):
            time.sleep(0.05)
        live.post(set=1, type="set_start")
        assert stays.next_state(lambda s: s["sets"])
    lines = [r.getMessage() for r in caplog.records]
    assert "rho/1 watcher joined (1)" in lines and "rho/1 watcher joined (2)" in lines
    assert "rho/1 watcher left (1)" in lines


def test_an_unknown_token_gets_the_usual_404_and_no_stream(tmp_path):
    with serve(tmp_path) as live:
        answer = httpx.get(f"{live.base}/api/zzzzzz/1/stream", timeout=WAIT)
        assert answer.status_code == 404
        assert answer.json() == {"error": "not_found", "detail": "not found"}
        answer = httpx.get(
            f"{live.base}/api/{live.tokens['family']}/9/stream", timeout=WAIT
        )
        assert answer.status_code == 404
