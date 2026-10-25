import copy
import json

import httpx
from fastapi.testclient import TestClient
from test_api import KICKOFF
from test_stream import serve
from test_teams import VALID

from match_tracker.limiter import Limiter
from match_tracker.server import create_app


def limiter(limit=3, window=60):
    clock = [0.0]
    return Limiter(limit, window, lambda: clock[0]), clock


def test_the_limit_is_per_address_and_slides_with_the_window():
    lim, clock = limiter()
    assert [lim.allow("a") for _ in range(4)] == [True, True, True, False]
    assert lim.allow("b")  # another address has its own count
    clock[0] = 59
    assert not lim.allow("a")
    clock[0] = 60  # the first request is a full window old
    assert lim.allow("a")


def test_a_refused_request_does_not_extend_the_block():
    lim, clock = limiter(limit=2)
    assert lim.allow("a")  # t=0
    clock[0] = 10
    assert lim.allow("a")
    for clock[0] in (20, 30, 40, 50):  # keeps trying, refused each time
        assert not lim.allow("a")
    clock[0] = 60  # only the request at 0 has expired, which is enough
    assert lim.allow("a")
    clock[0] = 61
    assert not lim.allow("a")
    clock[0] = 70  # the one at 10 expires
    assert lim.allow("a")


def test_the_table_does_not_grow_for_ever_under_a_scan():
    lim, clock = limiter(limit=1)
    for i in range(1200):
        lim.allow(f"scanner-{i}")
    clock[0] = 100
    lim.allow("someone")
    assert len(lim._hits) < 10


def app_with_clock(tmp_path, clock):
    (tmp_path / "teams").mkdir(exist_ok=True)
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(copy.deepcopy(VALID)))
    app = create_app(tmp_path / "teams", tmp_path / "data", now=lambda: clock[0])
    return TestClient(app), app.state.teams.tokens["rho"]


def unknown(client, i, page=False):
    return client.get(f"/{'' if page else 'api/'}zzzz{i:02d}")


def test_the_eleventh_unknown_token_in_a_minute_is_refused(tmp_path):
    client, _ = app_with_clock(tmp_path, [KICKOFF])
    first_ten = [unknown(client, i).status_code for i in range(10)]
    assert first_ten == [404] * 10
    answer = unknown(client, 10)
    assert answer.status_code == 429
    assert answer.json()["error"] == "rate_limited" and "detail" in answer.json()


def test_pages_and_the_api_share_the_count(tmp_path):
    client, _ = app_with_clock(tmp_path, [KICKOFF])
    for i in range(5):
        assert unknown(client, i).status_code == 404
        assert unknown(client, i, page=True).status_code == 404
    assert unknown(client, 99).status_code == 429
    assert unknown(client, 99, page=True).status_code == 429


def test_a_known_token_is_never_limited_even_while_blocked(tmp_path):
    client, tokens = app_with_clock(tmp_path, [KICKOFF])
    for i in range(11):
        unknown(client, i)
    assert unknown(client, 50).status_code == 429
    assert client.get(f"/api/{tokens['family']}/1").status_code == 200
    assert client.get(f"/{tokens['staff']}/1").status_code == 200
    body = {"set": 1, "type": "set_start", "team": "us"}
    assert client.post(f"/api/{tokens['staff']}/1/events", json=body).status_code == 201


def test_an_unknown_match_under_a_known_token_is_not_counted(tmp_path):
    client, tokens = app_with_clock(tmp_path, [KICKOFF])
    for _ in range(30):
        assert client.get(f"/api/{tokens['family']}/99").status_code == 404
    assert unknown(client, 0).status_code == 404


def test_favicon_names_no_token_and_is_not_counted(tmp_path):
    client, _ = app_with_clock(tmp_path, [KICKOFF])
    for _ in range(25):
        assert client.get("/favicon.ico").status_code == 204
    assert unknown(client, 0).status_code == 404


def test_the_block_ends_a_minute_after_the_counted_requests(tmp_path):
    clock = [KICKOFF]
    client, _ = app_with_clock(tmp_path, clock)
    for i in range(10):
        unknown(client, i)
    assert unknown(client, 10).status_code == 429
    clock[0] = KICKOFF.replace(minute=KICKOFF.minute + 1, second=1)
    assert unknown(client, 11).status_code == 404


def test_behind_a_proxy_each_forwarded_address_has_its_own_count(tmp_path):
    with serve(tmp_path) as live:

        def ask(forwarded, i):
            headers = {"X-Forwarded-For": forwarded} if forwarded else {}
            return httpx.get(f"{live.base}/api/zzzz{i:02d}", headers=headers, timeout=5)

        assert [ask("10.0.0.1", i).status_code for i in range(10)] == [404] * 10
        assert ask("10.0.0.1", 10).status_code == 429
        assert ask("10.0.0.2", 0).status_code == 404  # another visitor behind the proxy
        assert ask(None, 0).status_code == 404  # the proxy's own address, apart


def test_a_forged_forwarded_address_does_not_escape_the_limit(tmp_path):
    """A tunnel appends the visitor's address to what the visitor sent: the
    leftmost entry is the visitor's to choose, the last one is the tunnel's."""
    with serve(tmp_path) as live:

        def ask(i):
            forwarded = f"198.51.100.{i}, 203.0.113.7"  # forged, then the real one
            headers = {"X-Forwarded-For": forwarded}
            return httpx.get(f"{live.base}/api/zzzz{i:02d}", headers=headers, timeout=5)

        assert [ask(i).status_code for i in range(10)] == [404] * 10
        assert ask(10).status_code == 429
