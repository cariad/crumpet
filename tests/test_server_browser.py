from pathlib import Path
from select import select
from threading import Event

from notwork import claim_port
from playwright.sync_api import Page, Route
from pytest import mark, param

from crumpet import Server
from tests.pages import next_event, open_page, reloads, settle

# Scripts that set up pages with and without shared workers.
_INIT_SCRIPTS = [
    param(
        "",
        id="shared-worker",
    ),
    param(
        "delete window.SharedWorker;",
        id="no-shared-worker",
    ),
]


@mark.parametrize(
    "init_script",
    _INIT_SCRIPTS,
)
def test_server_leaves_open_pages_when_stopped(
    browser_page: Page,
    events_opened: Event,
    free_port: int,
    init_script: str,
    tmp_path: Path,
) -> None:
    """
    Assert that stopping a server leaves open pages as they are, with or without
    shared workers.
    """
    navigations: list[str] = []

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        browser_page.add_init_script(init_script)
        browser_page.goto(server.url)
        assert events_opened.wait(5)

        browser_page.on(
            "framenavigated",
            lambda frame: navigations.append(frame.url),
        )

    # Listen where the server was, to see the event stream try to reconnect.
    with claim_port(free_port) as claimed:
        for sock in claimed.sockets:
            sock.listen()

        reconnected, _, _ = select(claimed.sockets, [], [], 10)

    assert reconnected

    # Long enough for a reload to start.
    browser_page.wait_for_timeout(1000)

    result = navigations
    expect: list[str] = []

    assert result == expect


@mark.parametrize(
    "init_script",
    _INIT_SCRIPTS,
)
def test_server_reloads_pages_from_earlier_run(
    browser_page: Page,
    events_opened: Event,
    free_port: int,
    init_script: str,
    tmp_path: Path,
) -> None:
    """
    Assert that a page left open from an earlier server reloads once a new server
    starts on the same port, with or without shared workers.
    """
    index = tmp_path / "index.html"
    index.write_text("<p>Old</p>")

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        browser_page.add_init_script(init_script)
        browser_page.goto(server.url)
        assert events_opened.wait(5)

    index.write_text("<p>New</p>")

    with Server(
        tmp_path,
        port=free_port,
    ):
        browser_page.wait_for_selector(
            "text=New",
            timeout=10000,
        )

        result = browser_page.inner_text("p")

    expect = "New"

    assert result == expect


@mark.parametrize(
    "init_script",
    _INIT_SCRIPTS,
)
@mark.parametrize(
    "watch",
    [
        False,
        True,
    ],
)
def test_server_reloads_pages_still_loading(
    browser_page: Page,
    free_port: int,
    init_script: str,
    tmp_path: Path,
    watch: bool,
) -> None:
    """
    Assert that a page that's still loading when pages reload, from `reload()` or from
    watching, reloads, with or without shared workers.
    """
    index = tmp_path / "index.html"

    # The reload script can't run until this script loads.
    index.write_text('<script src="slow.js"></script><p>Old</p>')

    with (
        Server(
            tmp_path,
            port=free_port,
            watch=watch,
        ) as server,
        open_page(server.url) as events,
    ):
        if watch:
            settle(events)

        def reload_then_load(route: Route) -> None:
            index.write_text("<p>New</p>")

            if not watch:
                server.reload()

            while next_event(events) == b":\n":
                pass

            route.fulfill(body="")

        browser_page.route(
            "**/slow.js",
            reload_then_load,
        )

        browser_page.add_init_script(init_script)
        browser_page.goto(
            server.url,
            wait_until="commit",
        )

        browser_page.wait_for_selector(
            "text=New",
            timeout=5000,
        )

        result = browser_page.inner_text("p")

    expect = "New"

    assert result == expect


@mark.parametrize(
    "init_script",
    _INIT_SCRIPTS,
)
def test_server_reloads_pages_that_load_more_html(
    browser_page: Page,
    events_opened: Event,
    free_port: int,
    init_script: str,
    tmp_path: Path,
) -> None:
    """
    Assert that a page that loads more HTML with a script, more times than browsers
    allow connections to a server, reloads without its requests stalling, with or
    without shared workers.
    """
    (tmp_path / "index.html").write_text(
        "<script>\n"
        "  async function loadMore() {\n"
        "    for (let i = 0; i < 10; i++) {\n"
        '      const response = await fetch("more.html", {\n'
        "        signal: AbortSignal.timeout(5000),\n"
        "      });\n"
        "      const html = await response.text();\n"
        "      const more = document.createRange().createContextualFragment(html);\n"
        "      document.body.append(more);\n"
        "    }\n"
        "  }\n"
        "</script>\n",
    )

    (tmp_path / "more.html").write_text("<p>More</p>")

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        browser_page.add_init_script(init_script)
        browser_page.goto(server.url)
        browser_page.evaluate("loadMore()")
        result = reloads(
            browser_page,
            server,
            events_opened,
        )

    assert result


def test_server_reloads_pages_with_strict_csp(
    browser_page: Page,
    events_opened: Event,
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that a page whose Content-Security-Policy allows only scripts from its own
    origin reloads.
    """
    (tmp_path / "index.html").write_text(
        '<meta http-equiv="Content-Security-Policy" content="default-src \'self\'">',
    )

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        browser_page.goto(server.url)
        result = reloads(
            browser_page,
            server,
            events_opened,
        )

    assert result


def test_server_reloads_pages_with_base_elsewhere(
    browser_page: Page,
    busy_port: int,
    events_opened: Event,
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that a page whose `<base href>` points at another site reloads."""
    (tmp_path / "index.html").write_text(
        f'<base href="http://127.0.0.1:{busy_port}/">',
    )

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        browser_page.goto(server.url)
        result = reloads(
            browser_page,
            server,
            events_opened,
        )

    assert result
