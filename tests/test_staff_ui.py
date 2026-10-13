"""The staff screen in a real browser at phone size, against a real server.

Skipped where Playwright has no browser to launch."""

import json
from datetime import timedelta
from urllib.request import urlopen

from test_api import KICKOFF
from test_stream import serve
from test_teams import VALID

SCORE = "document.querySelector('.score b')?.innerText.includes"


def staff_url(live):
    return f"{live.base}/{live.tokens['staff']}/1"


def state_of(live):
    with urlopen(f"{live.base}/api/{live.tokens['staff']}/1") as answer:
        return json.load(answer)


def wait_score(page, text):
    page.wait_for_function(f"{SCORE}('{text}')")


def open_staff(page, live):
    page.goto(staff_url(live))
    page.wait_for_selector(".score")


def test_a_set_is_played_from_the_bench(tmp_path, page):
    with serve(tmp_path) as live:
        open_staff(page, live)
        assert page.inner_text(".start") == "Inizia set 1"
        assert all(b.is_disabled() for b in page.locator(".team, .to").all())
        page.click(".start")
        page.wait_for_selector(".players")
        page.click(".players button:has-text('Anna')")
        wait_score(page, "1 – 0")
        page.click(".team.them")
        wait_score(page, "1 – 1")
        page.click(".players button:has-text('Bea')")
        wait_score(page, "2 – 1")
        events = state_of(live)["events"]
        assert [(e["type"], e.get("team"), e.get("player")) for e in events] == [
            ("set_start", None, None),
            ("point", "us", "anna"),
            ("point", "them", None),
            ("point", "us", "bea"),
        ]
        page.click(".bar button:has-text('↶')")
        wait_score(page, "1 – 1")
        assert len(state_of(live)["events"]) == 3
    assert page.errors == []


def test_a_team_runs_out_of_timeouts_and_the_button_stops(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        open_staff(page, live)
        for expected in ("●○", "●●"):
            page.click(".to.them")
            page.wait_for_function(
                f"document.querySelector('.to.them .dots').innerText === '{expected}'"
            )
        assert page.locator(".to.them").is_disabled()
        assert page.locator(".to.us").is_enabled()


def test_the_winning_point_closes_the_set_and_waits_for_the_next(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        for _ in range(24):
            live.post(set=1, type="point", team="us")
        open_staff(page, live)
        page.click(".players button:has-text('Anna')")
        page.wait_for_selector(".start")
        assert page.inner_text(".start") == "Inizia set 2"
        assert "Set 2" in page.inner_text(".score")
        assert "(1–0)" in page.inner_text(".score")
        page.click(".bar button:has-text('‹')")  # back to set 1: closed, buttons off
        assert "✓" in page.inner_text(".score") and "past" in page.get_attribute(
            ".score", "class"
        )
        assert page.locator(".team.us").is_disabled()
        page.click(".bar button:has-text('Riapri')")
        page.wait_for_function("!document.querySelector('.team.us').disabled")
        assert state_of(live)["sets"][0]["closed"] is False


def test_a_set_is_closed_by_hand_and_the_match_ended(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        live.post(set=1, type="point", team="us")
        open_staff(page, live)
        page.click(".bar button:has-text('Chiudi set')")
        page.wait_for_selector(".start")
        assert state_of(live)["sets"][0]["closed"]
        # the screen is on the next set, which has not started: the match can end
        assert not page.locator(".bar button:has-text('Fine')").is_disabled()
        page.click(".bar button:has-text('Fine')")
        page.wait_for_function(
            "document.querySelector('.bar .wide').innerText === 'Riapri'"
        )
        assert state_of(live)["match"]["status"] == "over"
        assert [
            e["set"] for e in state_of(live)["events"] if e["type"] == "match_end"
        ] == [1]
        assert page.locator(".bar button:has-text('Fine')").is_disabled()


def test_the_fix_list_attributes_moves_and_deletes(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        live.post(set=1, type="point", team="us")
        live.post(set=1, type="timeout", team="us")
        live.post(set=1, type="timeout", team="them")
        live.post(set=1, type="timeout", team="them")
        open_staff(page, live)
        page.click(".bar button:has-text('⋯')")
        rows = page.locator(".menu li")
        assert rows.count() == 5
        assert rows.last.locator("button.drop").count() == 0  # set start: no delete
        point_row = rows.nth(3)
        point_row.locator("select").select_option("bea")
        page.wait_for_function(
            "[...document.querySelectorAll('.menu li')][3].querySelector('select').value === 'bea'"
        )
        assert state_of(live)["events"][1]["player"] == "bea"
        # the timeout of "us" cannot go to a team that has none left
        us_timeout = rows.nth(2).locator("select option[value=them]")
        assert us_timeout.is_disabled()
        rows.nth(0).locator("button.drop").click()
        page.wait_for_function("document.querySelectorAll('.menu li').length === 4")
        assert len(state_of(live)["events"]) == 4
        page.click(".menu header button")
        assert page.locator(".menu").count() == 0


def test_outside_the_window_the_screen_is_read_only(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        live.clock[0] = KICKOFF - timedelta(hours=3)
        page.clock.set_system_time(live.clock[0])
        open_staff(page, live)
        assert page.inner_text(".notice").startswith("Apre alle")
        assert all(
            b.is_disabled() for b in page.locator(".team, .to, .players button").all()
        )
        live.clock[0] = KICKOFF + timedelta(hours=25)
        page.clock.set_system_time(live.clock[0])
        page.reload()
        page.wait_for_selector(".notice")
        assert page.inner_text(".notice") == "Sola lettura"
        assert all(
            b.is_disabled() for b in page.locator(".team, .to, .players button").all()
        )
        page.click(".bar button:has-text('⋯')")  # the list can still be read
        assert page.locator(".menu li").count() == 1


def test_a_refused_tap_shows_the_reason_and_repairs_the_screen(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        open_staff(page, live)
        live.post(set=1, type="set_end")  # another phone closed the set meanwhile
        page.click(".team.us")
        page.wait_for_selector(".notice.error")
        assert page.inner_text(".notice.error") == "Il set è chiuso"
        page.wait_for_function("document.querySelector('.team.us').disabled")


def test_the_family_page_sees_what_the_staff_taps(tmp_path, browser):
    with serve(tmp_path) as live:
        family = browser.new_page(viewport={"width": 390, "height": 844})
        staff = browser.new_page(viewport={"width": 390, "height": 844})
        family.goto(f"{live.base}/{live.tokens['family']}/1")
        family.wait_for_selector(".box")
        open_staff(staff, live)
        staff.click(".start")
        staff.wait_for_selector(".players")
        staff.click(".players button:has-text('Anna')")
        family.wait_for_function(
            "document.querySelector('.big')?.innerText.includes('1 – 0')"
        )
        assert "Anna" in family.inner_text("ul.feed")
        family.close()
        staff.close()


def test_the_staff_screen_shows_what_another_device_records(tmp_path, page):
    with serve(tmp_path) as live:
        live.post(set=1, type="set_start")
        page.goto(staff_url(live) + "?poll=1")
        page.wait_for_selector(".score")
        live.post(set=1, type="point", team="us")
        page.wait_for_function(f"{SCORE}('1')", timeout=5000)
    assert page.errors == []


def test_the_staff_screen_says_when_the_connection_is_lost_and_when_it_is_back(
    tmp_path, page
):
    with serve(tmp_path) as live:
        page.goto(staff_url(live) + "?poll=1")
        page.wait_for_selector(".score")
        page.route("**/api/**", lambda route: route.abort())
        page.wait_for_selector(".notice", timeout=5000)
        assert "Connessione persa" in page.inner_text(".notice")
        page.unroute("**/api/**")
        page.wait_for_selector(".notice", state="detached", timeout=5000)
    assert page.errors == []


def test_a_demo_team_has_a_reset_that_asks_first_and_wipes_the_match(tmp_path, page):
    with serve(tmp_path) as live:
        live.team_file.write_text(json.dumps({**VALID, "demo": True}))
        live.post(set=1, type="set_start")
        live.post(set=1, type="point", team="us")
        open_staff(page, live)
        wait_score(page, "1")
        page.click("text=⋯")
        answers = iter([False, True])
        page.on(
            "dialog",
            lambda dialog: dialog.accept() if next(answers) else dialog.dismiss(),
        )
        page.click(".reset")  # refused: nothing changes
        assert state_of(live)["sets"][0]["score"]["us"] == 1
        page.click(".reset")
        page.wait_for_function(
            "!document.querySelector('.score b')?.innerText.includes('1')"
        )
        assert state_of(live)["sets"] == []
    assert page.errors == []


def test_a_team_that_is_not_a_demo_has_no_reset(tmp_path, page):
    with serve(tmp_path) as live:
        open_staff(page, live)
        page.click("text=⋯")
        assert page.locator(".reset").count() == 0
    assert page.errors == []
