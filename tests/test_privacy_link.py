"""The privacy notice is linked from the foot of the public pages; the pages that
edit leave it to the landing page."""

import pytest
from test_season import SALT
from test_stream import serve


@pytest.fixture
def live(tmp_path):
    with serve(tmp_path, salt=SALT) as live:
        yield live


def link(page):
    return page.locator("a[href='/privacy']")


def open_page(page, url):
    page.goto(url)
    page.wait_for_selector("#app > :not([hidden])")
    page.wait_for_timeout(200)


def test_the_public_pages_link_to_the_notice(page, live):
    family, staff = live.tokens["family"], live.tokens["staff"]
    for path in (f"/{family}", f"/{family}/1", f"/{staff}"):
        open_page(page, live.base + path)
        assert link(page).count() == 1, path
        assert link(page).inner_text() == "Informativa sulla privacy"
    assert page.errors == []


def test_the_staff_screen_keeps_its_layout_and_has_the_link_in_its_menu(page, live):
    open_page(page, f"{live.base}/{live.tokens['staff']}/1")
    assert link(page).count() == 0  # the screen is for scoring, not for reading
    page.click("text=⋯")
    page.wait_for_selector(".menu")
    assert link(page).count() == 1
    assert page.errors == []


def test_the_pages_that_edit_do_not_have_it(page, live):
    coach = live.teams.coach_token("rho")
    admin = live.teams.admin_token
    for path in (
        f"/{coach}",
        f"/{coach}/team",
        f"/{coach}/season",
        f"/{admin}",
        f"/{admin}/new",
    ):
        open_page(page, live.base + path)
        assert link(page).count() == 0, path
    assert page.errors == []
