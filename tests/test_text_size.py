"""How the pages hold up when a person has set larger text (the text size scales
the rem, which every size in style.css is in): nothing may push the page sideways
and the staff bar, the buttons of the set, must stay reachable.

Skipped where Playwright has no browser to launch."""

import pytest
from test_stream import serve

SCALES = (100, 150, 200)


def play(live):
    """Two closed sets and a third in play, so every part of the pages is there."""
    live.post(set=1, type="set_start")
    live.post(set=1, type="point", team="us", player="anna")
    live.post(set=1, type="set_end")
    live.post(set=2, type="set_start")
    live.post(set=2, type="point", team="them")
    live.post(set=2, type="set_end")
    live.post(set=3, type="set_start")
    live.post(set=3, type="point", team="us")


def paths(live):
    family, staff = live.tokens["family"], live.tokens["staff"]
    return [
        "/",
        f"/{family}",
        f"/{family}/1",
        f"/{staff}",
        f"/{staff}/1",
        f"/{staff}/1/players",
    ]


@pytest.mark.parametrize("scale", SCALES)
def test_no_page_is_pushed_sideways_by_larger_text(tmp_path, page, scale):
    with serve(tmp_path) as live:
        play(live)
        for path in paths(live):
            page.goto(live.base + path)
            page.wait_for_selector("#app > :not([hidden])")
            page.add_style_tag(content=f"html {{ font-size: {scale}% !important; }}")
            wider = page.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert not wider, f"{path} scrolls sideways at {scale}%"
    assert page.errors == []


@pytest.mark.parametrize("scale", SCALES)
def test_the_staff_bar_stays_in_reach_with_larger_text(tmp_path, page, scale):
    with serve(tmp_path) as live:
        play(live)
        page.goto(f"{live.base}/{live.tokens['staff']}/1")
        page.wait_for_selector(".bar")
        page.add_style_tag(content=f"html {{ font-size: {scale}% !important; }}")
        box = page.locator(".bar").bounding_box()
        assert (
            box["y"] >= 0 and box["y"] + box["height"] <= page.viewport_size["height"]
        )
        for button in page.locator(".bar button").all():
            assert button.evaluate("b => b.scrollWidth <= b.clientWidth + 1")
    assert page.errors == []
