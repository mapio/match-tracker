"""The operator's log: where it goes, how it rotates, and what it never holds."""

import json
import logging
import socket
import stat
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import KICKOFF
from test_shutdown import free_port
from test_teams import VALID

from match_tracker import oplog, views
from match_tracker.server import create_app
from match_tracker.teams import derive_tokens


@pytest.fixture
def handler(tmp_path):
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    made = oplog.setup(tmp_path / "logs")
    yield made
    root.removeHandler(made)
    made.close()
    root.handlers[:] = before
    root.setLevel(level)


def test_the_log_rotates_at_midnight_keeping_thirty_files(handler):
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == 30


def test_the_directory_and_the_file_are_private(tmp_path, handler):
    logging.getLogger("match-tracker").info("hello")
    handler.flush()
    mode = stat.S_IMODE
    assert mode((tmp_path / "logs").stat().st_mode) == 0o700
    assert mode((tmp_path / "logs" / "app.log").stat().st_mode) == 0o600


def test_a_new_file_after_a_rotation_is_private_too(tmp_path, handler):
    logging.getLogger("match-tracker").info("before")
    handler.doRollover()
    logging.getLogger("match-tracker").info("after")
    handler.flush()
    current = tmp_path / "logs" / "app.log"
    assert stat.S_IMODE(current.stat().st_mode) == 0o600
    assert len(list((tmp_path / "logs").iterdir())) == 2


def test_an_unhandled_error_is_logged_with_its_traceback_but_no_token(
    tmp_path, handler, monkeypatch
):
    (tmp_path / "teams").mkdir()
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(VALID))
    app = create_app(tmp_path / "teams", tmp_path / "data", now=lambda: KICKOFF)
    token = app.state.teams.tokens["rho"]["family"]

    def broken(*args):
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(views, "match_list", broken)
    monkeypatch.setattr(logging.getLogger("httpx"), "level", logging.WARNING)  # the
    # test client's own chatter, which names the URL
    client = TestClient(app, raise_server_exceptions=False)
    answer = client.get(f"/api/{token}")
    assert answer.status_code == 500
    assert answer.json()["error"] == "server_error"
    page = client.get(f"/{token}/1/players")  # not a coach: an ordinary 404
    assert page.status_code == 404
    handler.flush()
    text = (tmp_path / "logs" / "app.log").read_text()
    assert "ZeroDivisionError: boom" in text
    assert "unhandled error in /api/{token}" in text
    assert token not in text


def test_the_server_as_a_command_writes_no_address_and_no_link(tmp_path):
    (tmp_path / "teams").mkdir()
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(VALID))
    port = free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "match_tracker",
            "--teams",
            str(tmp_path / "teams"),
            "--data",
            str(tmp_path / "data"),
            "--logs",
            str(tmp_path / "logs"),
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    tokens = derive_tokens(["rho"])["rho"]
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                httpx.get(f"{base}/static/list.html", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        httpx.get(f"{base}/{tokens['family']}")
        httpx.get(f"{base}/api/{tokens['family']}/1")
        httpx.get(f"{base}/api/{tokens['staff']}/1")
        for _ in range(12):  # past the limit, so that is noted too
            httpx.get(f"{base}/api/zzzzzz")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(b"GET /nothing HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client.recv(4096)
    finally:
        server.terminate()
        output, _ = server.communicate(timeout=10)
    lines = (tmp_path / "logs" / "app.log").read_text().splitlines()
    # uvicorn says where it listens, which is ours, not a visitor's
    written = output.decode() + "\n".join(l for l in lines if "running on" not in l)
    assert output == b""  # nothing on the console, so nothing in the journal
    assert "started: 1 team(s) (rho)" in written
    assert "unknown-token limit" in written
    for secret in (*tokens.values(), "127.0.0.1", "zzzzzz", "/api/"):
        assert secret not in written
