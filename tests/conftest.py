import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

PHONE = {"width": 390, "height": 844}


@pytest.fixture(scope="session")
def browser():
    """One Chromium for the run; the browser tests skip where none can launch."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.skip(f"no browser to launch: {error}")
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport=PHONE)
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    yield page
    page.close()
