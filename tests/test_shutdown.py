"""The real server, started as a command: it must stop soon after SIGTERM, which
is what a test script or a service manager send, while a family has a
stream open. A stream is a response that never completes, and uvicorn waits for
those unless it is given a limit."""

import json
import signal
import socket
import subprocess
import sys
import time

import httpx
from test_teams import VALID

from match_tracker.teams import derive_tokens


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_server_stops_on_sigterm_with_a_stream_open(tmp_path):
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{port}/static/list.html", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        token = derive_tokens(["rho"])["rho"]["family"]
        # a plain socket, as curl or a browser would hold the stream open
        request = f"GET /api/{token}/1/stream HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(request.encode())
            received = b""
            while b"event: state" not in received:
                received += client.recv(4096)
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=6)
            except subprocess.TimeoutExpired:
                raise AssertionError(
                    "the server did not stop with a stream open"
                ) from None
    finally:
        if server.poll() is None:
            server.kill()
