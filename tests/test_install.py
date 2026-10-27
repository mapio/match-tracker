"""The app can be put on a phone's home screen: a manifest for each token, the
icons, and the page that says how. Nothing is stored on the device."""

import logging
from pathlib import Path

import pytest
from test_landing import make_client
from test_season import SALT, make
from test_stream import serve

import match_tracker

STATIC = Path(match_tracker.__file__).parent / "static"
APPS = {  # role: (name, short name, suffix of the icons' files)
    "family": ("MatchTracker", "MatchTracker", ""),
    "staff": ("MatchTrackerStaff", "MTStaff", "-staff"),
    "coach": ("MatchTrackerCoach", "MTCoach", "-coach"),
    "admin": ("MatchTrackerAdmin", "MTAdmin", "-admin"),
}
NEUTRAL = {
    name: (STATIC / "icons" / name).read_bytes()
    for _, _, suffix in APPS.values()
    for name in (f"icon-{size}{suffix}.png" for size in ("192", "512", "maskable-512"))
}


def manifest_of(client, token):
    return client.get(f"/{token}/manifest.webmanifest")


def test_every_role_gets_a_manifest_that_starts_on_its_own_page(tmp_path):
    client, teams = make(tmp_path)
    tokens = {
        "family": teams.tokens["rho"]["family"],
        "staff": teams.tokens["rho"]["staff"],
        "coach": teams.coach_token("rho"),
        "admin": teams.admin_token,
    }
    for role, token in tokens.items():
        answer = manifest_of(client, token)
        assert answer.status_code == 200
        assert answer.headers["content-type"] == "application/manifest+json"
        assert answer.headers["cache-control"] == "no-cache"
        body = answer.json()
        name, short_name, suffix = APPS[role]
        assert (body["name"], body["short_name"]) == (name, short_name), role
        assert [i["src"] for i in body["icons"]] == [
            f"/icons/icon-192{suffix}.png",
            f"/icons/icon-512{suffix}.png",
            f"/icons/icon-maskable-512{suffix}.png",
        ]
        assert body["display"] == "standalone"
        assert body["start_url"] == body["id"] == f"/{token}/"
        # a scope of the whole site would make the first installed app take
        # the links of every other role
        assert body["scope"] == body["start_url"]
        assert {(i["sizes"], i.get("purpose", "any")) for i in body["icons"]} == {
            ("192x192", "any"),
            ("512x512", "any"),
            ("512x512", "maskable"),
        }
        for icon in body["icons"]:
            assert client.get(icon["src"]).headers["content-type"] == icon["type"]


def test_the_page_answers_at_its_directory_too(tmp_path):
    client, tokens = make_client(tmp_path)
    for token in tokens.values():
        answer = client.get(f"/{token}/", follow_redirects=False)
        assert answer.status_code == 200
        assert answer.text == client.get(f"/{token}").text
    assert client.get("/nosuchtoken/", follow_redirects=False).status_code == 404


def test_the_manifest_does_not_take_the_place_of_a_match(tmp_path):
    client, tokens = make_client(tmp_path)
    family = tokens["family"]
    assert client.get(f"/{family}/1").status_code == 200
    assert "match.js" in client.get(f"/{family}/1").text
    assert client.get(f"/{family}/99").status_code == 404
    assert manifest_of(client, family).status_code == 200


def test_an_unknown_token_gets_the_usual_refusal_and_counts_against_the_limit(
    tmp_path,
):
    client, _ = make_client(tmp_path)
    for i in range(10):
        answer = manifest_of(client, f"zzzz{i:02d}")
        assert answer.status_code == 404 and "error.js" in answer.text
    assert manifest_of(client, "zzzz99").status_code == 429
    assert client.get("/zzzz98").status_code == 429  # one count, shared with the pages


def test_a_known_token_is_never_limited_even_while_blocked(tmp_path):
    client, tokens = make_client(tmp_path)
    for i in range(11):
        manifest_of(client, f"zzzz{i:02d}")
    assert manifest_of(client, tokens["family"]).status_code == 200


def test_no_token_reaches_the_log(tmp_path, caplog):
    client, tokens = make_client(tmp_path)
    seen = ["zzzz00", tokens["family"], tokens["staff"]]
    with caplog.at_level(logging.DEBUG):
        for token in seen:
            manifest_of(client, token)
        for i in range(12):  # past the limit, which notes its refusals
            manifest_of(client, f"zzzz{i:02d}")
    # the app's own records: the test client's library logs the URLs it fetches
    ours = [r.getMessage() for r in caplog.records if r.name == "match-tracker"]
    assert ours  # the limit's note
    assert not any(t in line for t in (*seen, "zzzz11") for line in ours)


def test_the_install_page_serves_without_a_token_and_is_not_counted(tmp_path):
    client, _ = make_client(tmp_path)
    for _ in range(25):
        answer = client.get("/install")
        assert answer.status_code == 200 and "install.js" in answer.text
    assert answer.headers["cache-control"] == "no-cache"
    assert client.get("/api/zzzzzz").status_code == 404  # the limit is untouched


def test_the_icons_are_the_neutral_ones_unless_the_operator_has_its_own(tmp_path):
    client, _ = make_client(tmp_path)
    for name, neutral in NEUTRAL.items():
        answer = client.get(f"/icons/{name}")
        assert answer.status_code == 200 and answer.content == neutral
        assert answer.headers["content-type"] == "image/png"
    (tmp_path / "teams" / "icon-192.png").write_bytes(b"team icon")
    (tmp_path / "teams" / "icon-512-coach.png").write_bytes(b"coach icon")
    assert client.get("/icons/icon-192.png").content == b"team icon"
    assert client.get("/icons/icon-512-coach.png").content == b"coach icon"
    assert client.get("/icons/icon-512.png").content == NEUTRAL["icon-512.png"]
    assert (
        client.get("/icons/icon-512-staff.png").content == NEUTRAL["icon-512-staff.png"]
    )


def test_only_the_named_icons_are_served(tmp_path):
    client, _ = make_client(tmp_path)
    (tmp_path / "teams" / "secret.png").write_bytes(b"x")
    for name in ("secret.png", "icon.svg", "icon-coach.svg", "..%2Frho.json"):
        assert client.get(f"/icons/{name}").status_code == 404


@pytest.fixture
def live(tmp_path):
    with serve(tmp_path, salt=SALT) as live:
        yield live


def test_the_pages_link_the_manifest_of_their_token_and_the_install_page(page, live):
    family, staff = live.tokens["family"], live.tokens["staff"]
    coach = live.teams.coach_token("rho")
    admin = live.teams.admin_token
    for path, token, role in (
        (f"/{family}", family, "family"),
        (f"/{family}/1", family, "family"),
        (f"/{staff}", staff, "staff"),
        (f"/{staff}/1", staff, "staff"),
        (f"/{family}/1/chart", family, "family"),
        (f"/{coach}", coach, "coach"),
        (f"/{coach}/season", coach, "coach"),
        (f"/{admin}", admin, "admin"),
    ):
        page.goto(live.base + path)
        page.wait_for_selector("#app > :not([hidden])")
        link = page.locator("link[rel=manifest]")
        assert link.get_attribute("href") == f"/{token}/manifest.webmanifest", path
        # Safari ignores the manifest: the page says the same for itself
        _, short_name, suffix = APPS[role]
        title = page.locator("meta[name=apple-mobile-web-app-title]")
        assert title.get_attribute("content") == short_name, path
        icon = page.locator("link[rel=apple-touch-icon]")
        assert icon.get_attribute("href") == f"/icons/icon-192{suffix}.png", path
        assert page.locator("meta[name=theme-color]").count() == 2
    page.goto(f"{live.base}/{family}")
    page.wait_for_selector("a[href^='/install']")
    page.click("a[href^='/install']")
    page.wait_for_selector("h1")
    assert (
        page.locator("h1").inner_text() == "Aggiungi MatchTracker alla schermata home"
    )
    assert page.locator("link[rel=manifest]").count() == 0
    assert page.errors == []


def test_a_wrong_link_gets_no_manifest_link(page, live):
    page.goto(f"{live.base}/zzzzzz")
    page.wait_for_selector("#app > *")
    assert page.locator("link[rel=manifest]").count() == 0
    page.goto(live.base)
    page.wait_for_selector("#app > *")
    assert page.locator("link[rel=manifest]").count() == 0


def test_nothing_is_kept_on_the_device():
    scripts = "".join(p.read_text(encoding="utf-8") for p in STATIC.glob("*.js"))
    for api in ("serviceWorker", "localStorage", "sessionStorage", "document.cookie"):
        assert api not in scripts, api
