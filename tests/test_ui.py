"""The pages in a real browser at phone size, against a real server.

Skipped where Playwright has no browser to launch."""

import re
from datetime import timedelta

from test_api import KICKOFF
from test_stream import serve


def test_the_list_shows_the_matches_with_a_maps_link(tmp_path, page):
    with serve(tmp_path) as live:
        page.goto(f"{live.base}/{live.tokens['family']}")
        page.wait_for_selector(".card")
        cards = page.locator(".card")
        assert cards.count() == 2
        assert "Opponent" in cards.nth(0).inner_text()
        assert "—" in cards.nth(0).inner_text()  # not played yet
        link = cards.nth(0).locator("a.where")
        assert "google.com/maps" in link.get_attribute("href")
        assert "Via%20Roma%201" in link.get_attribute("href")
        assert page.title() == "Volley Test"
    assert page.errors == []


def test_the_match_page_follows_the_staff_without_a_reload(tmp_path, page):
    with serve(tmp_path) as live:
        page.goto(f"{live.base}/{live.tokens['family']}/1")
        page.wait_for_selector(".box")
        assert page.inner_text(".box") == "Non ancora iniziata"  # the team's language
        live.post(set=1, type="set_start")
        for _ in range(2):
            live.post(set=1, type="point", team="us")
        live.post(set=1, type="point", team="them")
        live.post(set=1, type="timeout", team="them")
        page.wait_for_function(
            "document.querySelector('.big')?.innerText.includes('2 – 1')"
        )
        assert "IN DIRETTA" in page.inner_text("h1")
        assert page.inner_text(".timeouts").split() == ["○○", "●○"]
        assert page.locator("ul.feed").count() == 0  # no list of the latest points
    assert page.errors == []


def test_a_closed_set_goes_to_the_table(tmp_path, page):
    with serve(tmp_path) as live:
        page.goto(f"{live.base}/{live.tokens['family']}/1")
        live.post(set=1, type="set_start")
        for _ in range(25):
            live.post(set=1, type="point", team="us")
        live.post(set=2, type="set_start")
        for _ in range(14):
            live.post(set=2, type="point", team="them")
        page.wait_for_function(
            "document.querySelector('.big')?.innerText.includes('14')"
        )
        assert page.locator("table tr").count() == 2  # header and set 1
        assert page.locator("table td.win").inner_text() == "25"
        assert "Set 2" in page.inner_text(".sub")
        # when a set began and ended: under its number once closed, "since" while live
        span = page.locator("table td .pill").inner_text()
        assert re.fullmatch(r"\d\d[:.]\d\d–\d\d[:.]\d\d", span), span
        assert re.search(r"dalle \d\d[:.]\d\d", page.inner_text(".sub"))


def test_the_language_can_be_overridden(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        live.post(set=1, type="point", team="us")
        page.goto(f"{live.base}/{live.tokens['family']}/1?lang=en")
        page.wait_for_selector("h1 .live")
        assert page.inner_text("h1 .live") == "LIVE"
        assert page.evaluate("document.documentElement.lang") == "en"


def test_the_list_puts_the_next_match_on_top_then_the_results_newest_first(
    tmp_path, page
):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        live.post(set=1, type="point", team="us")
        live.clock[0] = KICKOFF + timedelta(hours=25)  # match 1 is over, 2 is next
        page.goto(f"{live.base}/{live.tokens['family']}")
        page.wait_for_selector(".card.featured")
        order = [
            node.inner_text().split("\n")[0]
            for node in page.locator("#app > .card, #app > h2").all()
        ]
        assert order == ["Other", "Risultati", "Opponent"]
        assert "featured" in page.locator(".card").nth(0).get_attribute("class")
        assert "featured" not in page.locator(".card").nth(1).get_attribute("class")


def test_a_live_match_comes_first_and_the_later_ones_follow(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        page.goto(f"{live.base}/{live.tokens['family']}")
        page.wait_for_selector(".card.featured .live")
        order = [
            node.inner_text().split("\n")[0].replace("IN DIRETTA", "")
            for node in page.locator("#app > .card, #app > h2").all()
        ]
        assert order == ["Opponent", "Da giocare", "Other"]


def test_no_page_shows_stray_null_or_false_text(tmp_path, page):
    with serve(tmp_path) as live:
        urls = [
            f"{live.base}/{live.tokens['family']}",
            f"{live.base}/{live.tokens['family']}/1",
            f"{live.base}/{live.tokens['staff']}",
            f"{live.base}/{live.tokens['staff']}/1",
        ]
        steps = [
            lambda: None,  # nothing recorded yet
            lambda: live.post(set=1, type="set_start"),  # no point, no closed set
            lambda: live.post(set=1, type="point", team="us"),
            lambda: live.post(
                set=1, type="set_end"
            ),  # a closed set, the next not started
        ]
        for step in steps:
            step()
            for url in urls:
                page.goto(url)
                page.wait_for_selector("#app > :not([hidden])")
                page.wait_for_timeout(200)
                text = page.inner_text("body")
                for stray in ("null", "undefined", "false", "NaN"):
                    assert stray not in text, f"{stray!r} on {url}: {text[:200]!r}"
    assert page.errors == []


def test_staff_pages_are_framed_and_titled_and_public_pages_are_not(tmp_path, page):
    frame = "getComputedStyle(document.documentElement, '::after').borderTopWidth"
    with serve(tmp_path) as live:
        for role, framed in (("staff", True), ("family", False)):
            for path in ("", "/1"):
                page.goto(f"{live.base}/{live.tokens[role]}{path}")
                page.wait_for_selector("#app > :not([hidden])")
                assert page.evaluate("document.documentElement.dataset.role") == role
                assert page.evaluate(frame) == ("5px" if framed else "0px")
                assert page.title().startswith("Staff · ") == framed


def test_the_list_shows_the_live_score(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        live.post(set=1, type="point", team="us")
        page.goto(f"{live.base}/{live.tokens['family']}")
        page.wait_for_selector(".card .live")
        assert "Set 1: 1 – 0" in page.locator(".card").nth(0).inner_text()


def test_a_page_for_an_unknown_token_is_a_404(tmp_path, page):
    with serve(tmp_path) as live:
        answer = page.goto(f"{live.base}/zzzzzz")
        assert answer.status == 404


def test_the_family_page_follows_the_match_even_if_the_stream_is_held_up(
    tmp_path, page
):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        page.route("**/stream", lambda route: route.abort())
        page.goto(f"{live.base}/{live.tokens['family']}/1?poll=1")
        page.wait_for_selector(".big")
        live.post(set=1, type="point", team="us")
        page.wait_for_function(
            "document.querySelector('.big')?.innerText.includes('1 – 0')",
            timeout=5000,
        )
    assert page.errors == []
