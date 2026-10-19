"""The chart of the points in a real browser at phone size, against a real server.

Skipped where Playwright has no browser to launch."""

import json
from datetime import timedelta

from test_season import SALT
from test_stream import serve


def chart_url(live, role="family", n=1):
    return f"{live.base}/{live.tokens[role]}/{n}/chart"


def post(live, **event):
    """An event twenty seconds after the last, so that the dots are apart."""
    live.clock[0] += timedelta(seconds=20)
    live.post(**event)


def play(live):
    post(live, set=1, type="set_start")
    post(live, set=1, type="point", team="us", player="anna")
    post(live, set=1, type="point", team="them")
    post(live, set=1, type="timeout", team="them")
    post(live, set=1, type="point", team="us")


def test_the_chart_draws_the_set_and_a_tap_on_a_dot_tells_the_point(tmp_path, page):
    with serve(tmp_path) as live:
        play(live)
        page.goto(chart_url(live))
        page.wait_for_selector("svg")
        assert page.locator(".dot.us").count() == 2
        assert page.locator(".dot.them").count() == 1
        assert page.locator(".timeout.them").count() == 1
        assert page.inner_text("h2 b").replace(" ", "") == "2–1"
        page.locator(".hit").nth(1).click()  # the first point of ours, on top of theirs
        tip = page.locator(".tip")
        assert tip.is_visible()
        assert tip.inner_text().startswith("1–0") and "Anna" in tip.inner_text()
        page.mouse.click(5, 5)
        assert tip.is_hidden()
    assert page.errors == []


def test_the_chart_follows_the_match_and_a_new_set_joins_the_menu(tmp_path, page):
    with serve(tmp_path) as live:
        play(live)
        page.goto(chart_url(live))
        page.wait_for_selector("svg")
        post(live, set=1, type="point", team="them")
        page.wait_for_function("document.querySelectorAll('.dot.them').length === 2")
        assert page.locator("select option").all_inner_texts() == [
            "Set 1",
            "Tutti i set",
        ]
        post(live, set=1, type="set_end")
        post(live, set=2, type="set_start")
        post(live, set=2, type="point", team="us")
        page.wait_for_function(
            "document.querySelectorAll('select option').length === 3"
        )
        assert page.inner_text("h2 span") == "Set 2"  # it opens on the set in play
        page.select_option("select", "all")
        assert page.locator("section").count() == 2
    assert page.errors == []


def test_the_bubble_of_a_point_of_theirs_says_no_false_or_null(tmp_path, page):
    with serve(tmp_path) as live:
        play(live)
        page.goto(chart_url(live))
        page.wait_for_selector("svg")
        page.locator(".hit").nth(0).dispatch_event("click")  # theirs, under ours
        text = page.locator(".tip").inner_text()
        assert text.startswith("1–1") and "false" not in text and "null" not in text
    assert page.errors == []


def test_the_scorers_are_not_in_the_bubble_when_the_team_hides_them(tmp_path, page):
    with serve(tmp_path) as live:
        raw = json.loads(live.team_file.read_text())
        live.team_file.write_text(json.dumps(raw | {"show_scorers": False}))
        play(live)
        page.goto(chart_url(live))
        page.wait_for_selector("svg")
        page.locator(".hit").nth(1).click()  # the first point of ours, on top of theirs
        assert page.locator(".tip").inner_text().startswith("1–0")
        assert "Anna" not in page.inner_text("body")
    assert page.errors == []


def test_the_staff_see_the_chart_with_the_names_and_the_match_pages_link_to_it(
    tmp_path, page
):
    with serve(tmp_path) as live:
        play(live)
        page.goto(chart_url(live, "staff"))
        page.wait_for_selector("svg")
        page.locator(".hit").nth(1).click()  # the first point of ours, on top of theirs
        assert "Anna" in page.locator(".tip").inner_text()
        page.goto(f"{live.base}/{live.tokens['family']}/1")
        page.wait_for_selector("a.chart-link")
        page.click("a.chart-link")
        page.wait_for_selector("svg")
        assert page.url.endswith("/1/chart")
    assert page.errors == []


def test_only_the_tokens_of_a_match_open_its_chart(tmp_path, page):
    with serve(tmp_path, salt=SALT) as live:
        coach = live.teams.coach_token("rho")
        assert page.goto(f"{live.base}/{coach}/1/chart").status == 404
        assert page.goto(f"{live.base}/zzzzzz/1/chart").status == 404
        assert page.goto(chart_url(live, n=9)).status == 404
        assert page.goto(chart_url(live)).status == 200


def test_a_match_not_started_says_so(tmp_path, page):
    with serve(tmp_path) as live:
        page.goto(chart_url(live))
        page.wait_for_selector(".notice")
        assert page.inner_text(".notice") == "Non ancora iniziata"
    assert page.errors == []


def test_a_phone_on_its_side_shows_the_whole_chart_without_scrolling(tmp_path, page):
    with serve(tmp_path) as live:
        play(live)
        page.set_viewport_size({"width": 740, "height": 360})
        page.goto(chart_url(live))
        page.wait_for_selector("svg")
        page.wait_for_timeout(200)
        scrolls = page.evaluate(
            "document.documentElement.scrollHeight > document.documentElement.clientHeight"
        )
        assert not scrolls
        bottom = page.evaluate(
            "document.querySelector('svg').getBoundingClientRect().bottom"
        )
        assert bottom <= 360
    assert page.errors == []
