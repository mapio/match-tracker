"""The pages load nothing from another host: a visitor's address goes to nobody
but this server (and the proxy in front of it). Pico and the icon font are copied
under static/vendor/ for that."""

import re
from pathlib import Path

from test_api import make_client

import match_tracker

STATIC = Path(match_tracker.__file__).parent / "static"
LOADING = re.compile(
    r"<(?:link|script|img|iframe|source)\b[^>]*?(?:src|href)=\"(?:https?:)?//",
    re.IGNORECASE,
)


def test_the_check_sees_a_resource_from_another_host():
    cdn = '<link rel="stylesheet" href="https://cdn.example.org/x.css">'
    assert LOADING.search(cdn) and LOADING.search(cdn.replace("https:", ""))
    assert not LOADING.search('<link rel="stylesheet" href="/static/style.css">')
    assert not LOADING.search('<a href="https://example.org/">a link, not a load</a>')


def test_no_page_loads_a_resource_from_another_host():
    for page in STATIC.glob("*.html"):
        assert not LOADING.search(page.read_text(encoding="utf-8")), page.name


def test_no_script_fetches_from_another_host():
    for script in STATIC.glob("*.js"):
        text = script.read_text(encoding="utf-8")
        assert not re.search(r"fetch\(\s*[`\"']https?:", text), script.name


def test_the_vendored_files_are_served_and_have_their_licences(tmp_path):
    client, _ = make_client(tmp_path)
    for path in (
        "/static/vendor/pico/pico.min.css",
        "/static/vendor/bootstrap-icons/bootstrap-icons.min.css",
        "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
        "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff",
        "/static/vendor/pico/LICENSE.md",
        "/static/vendor/bootstrap-icons/LICENSE",
    ):
        assert client.get(path).status_code == 200, path
    css = client.get("/static/vendor/bootstrap-icons/bootstrap-icons.min.css").text
    for font in re.findall(r"url\(\"([^\"?]+)", css):  # the paths the CSS asks for
        assert client.get(f"/static/vendor/bootstrap-icons/{font}").status_code == 200
