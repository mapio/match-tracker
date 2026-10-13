import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import KICKOFF
from test_stream import serve
from test_teams import VALID

from match_tracker.server import create_app
from match_tracker.teams import ConfigError, Team

CHAT = "https://wa.me/390000000000"


def make_client(tmp_path, **team):
    (tmp_path / "teams").mkdir(parents=True, exist_ok=True)
    raw = copy.deepcopy(VALID) | team
    (tmp_path / "teams" / "rho.json").write_text(json.dumps(raw))
    app = create_app(tmp_path / "teams", tmp_path / "data", now=lambda: KICKOFF)
    return TestClient(app), app.state.teams.tokens["rho"]


@pytest.mark.parametrize("bad", ["", "wa.me/39", "ftp://wa.me/39", "https://", 5])
def test_a_contact_must_be_an_http_url(bad):
    with pytest.raises(ConfigError, match="contact: expected an http or https URL"):
        Team.parse("rho", {**VALID, "contact": bad})


def test_the_contacts_list_the_teams_that_have_one_and_no_token(tmp_path):
    client, tokens = make_client(tmp_path, contact=CHAT)
    answer = client.get("/api/contacts")
    assert answer.status_code == 200
    contact = {"url": "/chat/rho", "whatsapp": True}
    assert answer.json() == [{"name": "Volley Test", "lang": "it", "contact": contact}]
    assert tokens["family"] not in answer.text and tokens["staff"] not in answer.text
    assert "390000000000" not in answer.text  # the address is only in the redirect
    client, _ = make_client(tmp_path / "other")
    assert client.get("/api/contacts").json() == []


def test_the_team_object_carries_the_contact_only_when_set(tmp_path):
    client, tokens = make_client(tmp_path, contact=CHAT)
    answer = client.get(f"/api/{tokens['family']}")
    assert answer.json()["team"]["contact"] == {"url": "/chat/rho", "whatsapp": True}
    assert "390000000000" not in answer.text
    client, tokens = make_client(tmp_path / "other")
    assert "contact" not in client.get(f"/api/{tokens['family']}").json()["team"]


def test_the_chat_path_redirects_to_the_contact_and_only_for_a_team_that_has_one(
    tmp_path,
):
    client, _ = make_client(tmp_path, contact=CHAT)
    answer = client.get("/chat/rho", follow_redirects=False)
    assert answer.status_code == 302 and answer.headers["location"] == CHAT
    assert answer.headers["cache-control"] == "no-store"
    for path in ("/chat/nobody", "/chat/RHO"):
        missing = client.get(path, follow_redirects=False)
        assert missing.status_code == 404 and "error.js" in missing.text
    client, _ = make_client(tmp_path / "other")
    assert client.get("/chat/rho", follow_redirects=False).status_code == 404


@pytest.mark.parametrize("path", ["/", "/zzzzzz", "/zzzzzz/1", "/x/y/z"])
def test_a_page_request_gets_the_error_page_and_the_api_stays_json(tmp_path, path):
    client, tokens = make_client(tmp_path)
    answer = client.get(path)
    assert answer.status_code == (200 if path == "/" else 404)
    assert answer.headers["content-type"].startswith("text/html")
    assert "error.js" in answer.text and answer.headers["cache-control"] == "no-cache"
    wrong_match = client.get(f"/{tokens['family']}/99")
    assert wrong_match.status_code == 404 and "error.js" in wrong_match.text
    assert client.get(f"/api/zzzzzz{path}").json()["detail"]


def test_a_rate_limited_page_is_the_error_page_with_429(tmp_path):
    client, _ = make_client(tmp_path)
    for i in range(10):
        client.get(f"/zzzz{i:02d}")
    answer = client.get("/zzzz99")
    assert answer.status_code == 429 and "error.js" in answer.text
    assert client.get("/api/zzzz99").json()["error"] == "rate_limited"


def test_the_landing_page_lists_the_teams_with_their_contact(tmp_path, page):
    with serve(tmp_path) as live:
        (tmp_path / "teams" / "rho.json").write_text(
            json.dumps({**VALID, "contact": CHAT})
        )
        page.goto(live.base + "/?lang=it")
        page.wait_for_selector("a[role=button]")
        button = page.locator("a[role=button]")
        assert button.inner_text().split("\n") == ["Volley Test", "Scrivi su WhatsApp"]
        assert button.get_attribute("href") == "/chat/rho"
        assert (
            "whatsapp" in button.get_attribute("class")
            and button.locator("svg").count()
        )
        assert "390000000000" not in page.content()
        body = page.inner_text("body")
        assert live.tokens["family"] not in body and live.tokens["staff"] not in body
        no_team = "a[href^='/']:not([href^='/chat/']):not([href='/privacy'])"
        assert page.locator(no_team).count() == 0  # no link into a team
    assert page.errors == []


def test_a_wrong_token_shows_the_list_and_a_wrong_match_only_that_team(tmp_path, page):
    with serve(tmp_path) as live:
        (tmp_path / "teams" / "rho.json").write_text(
            json.dumps({**VALID, "contact": CHAT})
        )
        page.goto(f"{live.base}/zzzzzz")
        page.wait_for_selector("a[role=button]")
        assert "non funziona" in page.inner_text(
            "h1"
        ) or "does not work" in page.inner_text("h1")
        assert page.locator("a[role=button]").get_attribute("href") == "/chat/rho"

        page.goto(f"{live.base}/{live.tokens['family']}/99")
        page.wait_for_selector("a[role=button]")
        assert (
            page.locator("h1").inner_text() == "Questo link non funziona"
        )  # the team's it
        assert page.locator("a[role=button]").get_attribute("href") == "/chat/rho"
        back = page.get_by_text("Tutte le partite")
        assert back.get_attribute("href") == f"/{live.tokens['family']}"
    assert page.errors == []


def test_without_a_contact_the_pages_say_so(tmp_path, page):
    with serve(tmp_path) as live:
        page.goto(live.base + "/?lang=it")
        page.wait_for_function("document.querySelector('#app p + p')")
        assert page.locator("a[role=button]").count() == 0
        assert "Chiedi a chi ti ha mandato il link" in page.inner_text("#app")
    assert page.errors == []


def test_a_rate_limited_page_says_to_wait_and_shows_no_contact(tmp_path, page):
    with serve(tmp_path) as live:
        (tmp_path / "teams" / "rho.json").write_text(
            json.dumps({**VALID, "contact": CHAT})
        )
        for i in range(10):
            httpx.get(f"{live.base}/zzzz{i:02d}")
        page.goto(f"{live.base}/zzzz99?lang=it")
        page.wait_for_function("document.querySelector('h1')?.innerText")
        assert page.inner_text("h1") == "Troppi tentativi"
        assert page.locator("a[role=button]").count() == 0
    assert page.errors == []


def test_a_demo_team_can_be_reset_by_staff_and_no_other_can(tmp_path):
    client, tokens = make_client(tmp_path, demo=True)
    staff, family = tokens["staff"], tokens["family"]
    body = {"set": 1, "type": "set_start"}
    assert client.post(f"/api/{staff}/1/events", json=body).status_code == 201
    assert client.get(f"/api/{staff}").json()["team"]["demo"] is True
    assert client.post(f"/api/{family}/1/reset").status_code == 404  # staff only
    answer = client.post(f"/api/{staff}/1/reset")
    assert answer.status_code == 200 and answer.json()["sets"] == []
    assert client.get(f"/api/{family}/1").json()["sets"] == []
    assert not (tmp_path / "data" / "rho" / "1.json").exists()
    assert client.post(f"/api/{staff}/1/events", json=body).status_code == 201

    other, tokens = make_client(tmp_path / "other")
    assert other.post(f"/api/{tokens['staff']}/1/events", json=body).status_code == 201
    assert other.post(f"/api/{tokens['staff']}/1/reset").status_code == 404
    assert "demo" not in other.get(f"/api/{tokens['staff']}").json()["team"]
    assert len(other.get(f"/api/{tokens['staff']}/1").json()["sets"]) == 1


def test_demo_must_be_true_or_false():
    with pytest.raises(ConfigError, match="demo: expected true or false"):
        Team.parse("rho", {**VALID, "demo": "yes"})


def test_the_privacy_notice_is_the_file_beside_the_team_files(tmp_path):
    client, _ = make_client(tmp_path)
    (tmp_path / "teams" / "privacy.html").write_text("<h1>Our notice</h1>")
    for _ in range(25):  # no token, so it does not count against the limit
        page = client.get("/privacy")
        assert page.status_code == 200 and "Our notice" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/api/zzzzzz00").status_code == 404  # the limit is untouched


def test_without_a_notice_the_privacy_page_is_not_found(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/privacy").status_code == 404


def test_the_demo_page_is_the_file_beside_the_team_files_if_there_is_one(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/demo").status_code == 404
    (tmp_path / "teams" / "demo.html").write_text("<h1>The demo</h1>")
    for _ in range(25):  # no token, so it does not count against the limit
        page = client.get("/demo")
        assert page.status_code == 200 and "The demo" in page.text
    assert client.get("/api/zzzzzz00").status_code == 404  # the limit is untouched


def test_the_landing_and_the_error_pages_link_to_the_notice(tmp_path, page):
    with serve(tmp_path) as live:
        for path in ("/?lang=it", "/zzzzzz?lang=it"):
            page.goto(live.base + path)
            page.wait_for_selector("h1")
            link = page.get_by_text("Informativa sulla privacy")
            assert link.get_attribute("href") == "/privacy"
    assert page.errors == []
