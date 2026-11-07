from collections.abc import Callable, Generator
from contextlib import closing, contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from os import utime
from pathlib import Path
from shutil import rmtree
from socket import (
    AF_INET,
    AF_INET6,
    IPPROTO_IPV6,
    IPV6_V6ONLY,
    SO_REUSEADDR,
    SOCK_STREAM,
    SOL_SOCKET,
    create_connection,
    socket,
)
from threading import Event, Thread
from typing import NamedTuple

from notwork import PortInUseError, PortOutOfRangeError
from playwright.sync_api import Page, Route
from pytest import MonkeyPatch, mark, param, raises

from crumpet import Server
from tests.marks import needs_dual_stack, needs_ipv6
from tests.pages import get, next_event, open_page, reloads, settle

# Sent with every response.
_NO_STORE = ("Cache-Control", "no-store")


class _Response(NamedTuple):
    """A response, without its date."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes


def _get(url: str) -> _Response:
    """Get `url`, without following a redirect."""
    with get(url) as response:
        return _Response(
            status=response.status,
            headers=[
                (name, value) for name, value in response.getheaders() if name != "Date"
            ],
            body=response.read(),
        )


@contextmanager
def _listen(
    host: str,
    *,
    dual_stack: bool = False,
    reuse_address: bool = False,
) -> Generator[int]:
    """
    Listen on `host` and yield the port.

    Args:
        host: Address to listen on.
        dual_stack: Whether or not IPv6/IPv6 dual stack is available.
        reuse_address: Whether or not to allow reusing ports.
    """
    family = AF_INET6 if ":" in host else AF_INET

    with socket(family, SOCK_STREAM) as sock:
        sock.setsockopt(
            SOL_SOCKET,
            SO_REUSEADDR,
            reuse_address,
        )

        if family == AF_INET6:
            sock.setsockopt(
                IPPROTO_IPV6,
                IPV6_V6ONLY,
                not dual_stack,
            )

        sock.bind((host, 0))
        sock.listen()

        yield sock.getsockname()[1]


@contextmanager
def _serve_like_python(directory: Path) -> Generator[str]:
    """Serve `directory` the way `python -m http.server` does, and yield its URL."""
    with ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(
            SimpleHTTPRequestHandler,
            directory=directory,
        ),
    ) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()

        try:
            yield f"http://127.0.0.1:{server.server_port}/"

        finally:
            server.shutdown()
            thread.join()


@mark.parametrize(
    (
        "path",
        "file",
        "content",
    ),
    [
        (
            "",
            "index.html",
            "<!DOCTYPE html><html><head></head><body><p>Index</p></body></html>",
        ),
        (
            "",
            "index.htm",
            "<p>Index without head or body</p>",
        ),
        (
            "PAGE.HTML",
            "PAGE.HTML",
            "<HTML><HEAD></HEAD><BODY><P>Upper case</P></BODY></HTML>",
        ),
        (
            "large.html",
            "large.html",
            "<p>Large</p>" * 100_000,
        ),
        # A directory without an index page is listed.
        (
            "",
            "notes.txt",
            "Notes",
        ),
    ],
)
def test_server_adds_reload_script_to_html(
    content: str,
    file: str,
    free_port: int,
    path: str,
    tmp_path: Path,
) -> None:
    """
    Assert that a server serves HTML files and listings like Python's own server does,
    with the reload script appended, however the HTML is written.
    """
    (tmp_path / file).write_text(content)
    script = (
        f'<script src="http://localhost:{free_port}/__crumpet__/reload.js?reloads=0">'
        "</script>"
    ).encode()

    with (
        Server(
            tmp_path,
            port=free_port,
        ) as server,
        _serve_like_python(tmp_path) as python_url,
    ):
        result = _get(server.url + path)
        python = _get(python_url + path)

    length = str(len(python.body) + len(script))

    expect = _Response(
        status=python.status,
        headers=[
            (
                name,
                length if name == "Content-Length" else value,
            )
            for name, value in python.headers
        ]
        + [_NO_STORE],
        body=python.body + script,
    )

    assert result == expect


def test_server_does_not_watch_by_default(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that a server doesn't reload open pages when a file changes by default."""
    with (
        Server(
            tmp_path,
            port=free_port,
        ) as server,
        open_page(server.url) as page,
    ):
        (tmp_path / "notes.txt").write_text("Notes")
        result = next_event(page)

    expect = b":\n"

    assert result == expect


def test_server_frees_port_when_closed(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that closing a server that hasn't been entered frees its port, so that
    another server can start on it straight away.
    """
    Server(
        tmp_path,
        port=free_port,
    ).close()

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = _get(server.url).status

    expect = 200

    assert result == expect


def test_server_frees_port_when_closed_while_entered(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that closing a server inside its `with` block frees its port, and that
    leaving the block afterwards is safe.
    """
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        server.close()

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = _get(server.url).status

    expect = 200

    assert result == expect


def test_server_frees_port_when_leaving(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that leaving a server's `with` block frees its port, so that another server
    can start on it straight away, even with pages still connected to the first.
    """
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        page = create_connection(
            ("localhost", free_port),
            timeout=5,
        )

        # Connections are accepted in order, so serving this one means that `page` was
        # accepted too.
        _get(server.url)

    with (
        page,
        Server(
            tmp_path,
            port=free_port,
        ) as server,
    ):
        result = _get(server.url).status

    expect = 200

    assert result == expect


def test_server_frees_port_when_watching_fails(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that a server that can't start watching frees its port, so that another
    server can start on it straight away.
    """
    public = tmp_path / "public"
    public.mkdir()

    server = Server(
        public,
        port=free_port,
        watch=True,
    )

    public.rmdir()

    with (
        raises(FileNotFoundError),
        server,
    ):
        pass

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = _get(server.url).status

    expect = 200

    assert result == expect


@mark.parametrize(
    (
        "host",
        "dual_stack",
    ),
    [
        (
            "127.0.0.1",
            False,
        ),
        param(
            "::1",
            False,
            marks=needs_ipv6,
        ),
        (
            "0.0.0.0",
            False,
        ),
        # Every address, IPv4 and IPv6, as `python -m http.server` listens.
        param(
            "::",
            True,
            marks=needs_dual_stack,
        ),
        # Every IPv6 address, ::1 included.
        param(
            "::",
            False,
            marks=needs_ipv6,
        ),
    ],
)
@mark.parametrize(
    "reuse_address",
    [
        False,
        True,
    ],
)
def test_server_raises_for_busy_port(
    dual_stack: bool,
    host: str,
    reuse_address: bool,
    tmp_path: Path,
) -> None:
    """Assert that creating a server on a busy port raises `PortInUseError`."""
    with (
        _listen(
            host,
            dual_stack=dual_stack,
            reuse_address=reuse_address,
        ) as port,
        raises(PortInUseError) as ex,
    ):
        Server(
            tmp_path,
            port=port,
        )

    result = ex.value.port
    expect = port

    assert result == expect


def test_server_raises_for_missing_directory(tmp_path: Path) -> None:
    """
    Assert that creating a server for a directory that doesn't exist raises
    `FileNotFoundError`, naming the directory as it was given.
    """
    directory = tmp_path / "missing"

    with raises(FileNotFoundError) as ex:
        Server(
            directory,
            port=8000,
        )

    result = ex.value.filename
    expect = str(directory)

    assert result == expect


def test_server_raises_for_non_directory(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Assert that creating a server for a path that isn't a directory raises
    `NotADirectoryError`, naming the path as it was given.
    """
    (tmp_path / "file.md").touch()
    monkeypatch.chdir(tmp_path)

    with raises(NotADirectoryError) as ex:
        Server(
            "file.md",
            port=8000,
        )

    result = ex.value.filename
    expect = "file.md"

    assert result == expect


@mark.parametrize(
    "port",
    [
        0,
        65536,
    ],
)
def test_server_raises_for_port_out_of_range(
    port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that creating a server on a port that isn't 1-65535 raises
    `PortOutOfRangeError`.
    """
    with raises(PortOutOfRangeError) as ex:
        Server(
            tmp_path,
            port=port,
        )

    result = str(ex.value)
    expect = f"Port {port} must be 1-65535"

    assert result == expect


@mark.parametrize(
    "path",
    [
        "__crumpet__/notes.txt",
        "%5f_crumpet__/notes.txt",
        "docs/../__crumpet__/notes.txt",
        "__CRUMPET__/notes.txt",
        "link/notes.txt",
        "__crumpet__/link.txt",
    ],
)
def test_server_refuses_reserved_paths(
    free_port: int,
    path: str,
    tmp_path: Path,
) -> None:
    """Assert that a file under the reserved prefix isn't served."""
    (tmp_path / "__crumpet__").mkdir()
    (tmp_path / "__crumpet__" / "notes.txt").write_text("Notes")
    (tmp_path / "link").symlink_to("__crumpet__")
    (tmp_path / "notes.txt").write_text("Notes")
    (tmp_path / "__crumpet__" / "link.txt").symlink_to("../notes.txt")

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = _get(server.url + path).status

    expect = 404

    assert result == expect


@mark.parametrize(
    "watch",
    [
        False,
        True,
    ],
)
def test_server_reload_reloads_open_pages(
    free_port: int,
    tmp_path: Path,
    watch: bool,
) -> None:
    """Assert that `reload()` tells every open page to reload."""
    with (
        Server(
            tmp_path,
            port=free_port,
            watch=watch,
        ) as server,
        open_page(server.url) as first,
        open_page(server.url) as second,
    ):
        if watch:
            settle(first)
            settle(second)

        server.reload()

        result = [
            next_event(first),
            next_event(second),
        ]

    expect = [
        b"data: \n",
        b"data: \n",
    ]

    assert result == expect


@mark.parametrize(
    (
        "path",
        "change",
    ),
    [
        # Files
        (
            "new.txt",
            partial(Path.write_text, data="New"),
        ),
        (
            "notes.txt",
            partial(Path.write_text, data="Changed"),
        ),
        (
            "notes.txt",
            Path.unlink,
        ),
        (
            "notes.txt",
            partial(Path.rename, target="renamed.txt"),
        ),
        # Directories
        (
            "new",
            Path.mkdir,
        ),
        (
            "docs",
            partial(utime, times=(0, 0)),
        ),
        (
            "empty",
            Path.rmdir,
        ),
        (
            "docs",
            partial(Path.rename, target="renamed"),
        ),
        # Deep and hidden
        (
            "docs/deep/deeper/deepest.txt",
            partial(Path.write_text, data="Changed"),
        ),
        (
            ".git/HEAD",
            partial(Path.write_text, data="Changed"),
        ),
    ],
)
def test_server_reloads_after_change(
    change: Callable[[Path], object],
    free_port: int,
    monkeypatch: MonkeyPatch,
    path: str,
    tmp_path: Path,
) -> None:
    """Assert that a watching server reloads every open page after any change."""
    (tmp_path / "notes.txt").write_text("Notes")
    (tmp_path / "empty").mkdir()
    (tmp_path / "docs" / "deep" / "deeper").mkdir(parents=True)
    (tmp_path / "docs" / "deep" / "deeper" / "deepest.txt").write_text("Deepest")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("Head")
    monkeypatch.chdir(tmp_path)

    with (
        Server(
            tmp_path,
            port=free_port,
            watch=True,
        ) as server,
        open_page(server.url) as first,
        open_page(server.url) as second,
    ):
        settle(first)
        settle(second)
        change(Path(path))

        result = [
            next_event(first),
            next_event(second),
        ]

    expect = [
        b"data: \n",
        b"data: \n",
    ]

    assert result == expect


def test_server_reloads_after_replacing_directory(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that a watching server keeps reloading open pages after its directory is
    deleted and created again.
    """
    public = tmp_path / "public"
    public.mkdir()

    with (
        Server(
            public,
            port=free_port,
            watch=True,
        ) as server,
        open_page(server.url) as page,
    ):
        settle(page)
        rmtree(public)
        settle(page)

        public.mkdir()
        (public / "index.html").write_text("Built")
        created = next_event(page)
        settle(page)

        (public / "index.html").write_text("Rebuilt")
        changed = next_event(page)

    result = [
        created,
        changed,
    ]

    expect = [
        b"data: \n",
        b"data: \n",
    ]

    assert result == expect


# A burst ends when no change comes for 50 milliseconds, and Windows can pause that
# long between writes, such as to scan each new file. The burst then splits in two,
# so pages reload twice. That's fine, but it fails the test, so try again.
@mark.flaky(reruns=2)
def test_server_reloads_once_per_burst(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that a watching server reloads open pages once per burst of changes."""
    with (
        Server(
            tmp_path,
            port=free_port,
            watch=True,
        ) as server,
        open_page(server.url) as page,
    ):
        settle(page)

        for index in range(20):
            (tmp_path / f"{index}.html").write_text("Built")

        # A second reload would come before the next heartbeat.
        result = [
            next_event(page),
            next_event(page),
        ]

    expect = [
        b"data: \n",
        b":\n",
    ]

    assert result == expect


def test_server_reloads_once_per_replacement(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that a watching server reloads open pages once when its directory is
    deleted and created again in one burst.
    """
    public = tmp_path / "public"
    public.mkdir()

    with (
        Server(
            public,
            port=free_port,
            watch=True,
        ) as server,
        open_page(server.url) as page,
    ):
        settle(page)
        rmtree(public)
        public.mkdir()

        # A second reload would come before the next heartbeat.
        result = [
            next_event(page),
            next_event(page),
        ]

    expect = [
        b"data: \n",
        b":\n",
    ]

    assert result == expect


@mark.parametrize(
    "init_script",
    [
        param(
            "",
            id="shared-worker",
        ),
        param(
            "delete window.SharedWorker;",
            id="no-shared-worker",
        ),
    ],
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
    [
        param(
            "",
            id="shared-worker",
        ),
        param(
            "delete window.SharedWorker;",
            id="no-shared-worker",
        ),
    ],
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


def test_server_raises_when_entered_after_closing(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that entering a server that's been closed raises `RuntimeError`."""
    server = Server(
        tmp_path,
        port=free_port,
    )

    server.close()

    with (
        raises(RuntimeError) as ex,
        server,
    ):
        pass

    result = str(ex.value)
    expect = "A closed server can't be entered"

    assert result == expect


def test_server_raises_when_entered_again(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that entering a server that's been left raises `RuntimeError`."""
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        pass

    with (
        raises(RuntimeError) as ex,
        server,
    ):
        pass

    result = str(ex.value)
    expect = "A server can only be entered once"

    assert result == expect


@mark.parametrize(
    "path",
    [
        "",
        "notes.txt",
    ],
)
def test_server_serves_404s_while_directory_is_gone(
    free_port: int,
    path: str,
    tmp_path: Path,
) -> None:
    """Assert that a watching server serves 404s while its directory is gone."""
    public = tmp_path / "public"
    public.mkdir()
    (public / "notes.txt").write_text("Notes")

    with (
        Server(
            public,
            port=free_port,
            watch=True,
        ) as server,
        open_page(server.url) as page,
    ):
        settle(page)
        rmtree(public)

        # Stops while waiting for the directory to come back.
        settle(page)

        result = _get(server.url + path).status

    expect = 404

    assert result == expect


@mark.parametrize(
    "path",
    [
        # A directory named without a trailing slash is redirected.
        "docs",
        "notes.txt",
        "missing.txt",
        # Symlinks are followed wherever they lead.
        "linked.txt",
        "linked/elsewhere.txt",
    ],
)
def test_server_serves_like_python(
    free_port: int,
    path: str,
    served: Path,
) -> None:
    """
    Assert that a server serves files, redirects and errors the way Python's own server
    does.
    """
    with (
        Server(
            served,
            port=free_port,
        ) as server,
        _serve_like_python(served) as python_url,
    ):
        result = _get(server.url + path)
        python = _get(python_url + path)

    expect = python._replace(
        headers=[*python.headers, _NO_STORE],
    )

    assert result == expect


@mark.parametrize(
    "host",
    [
        "127.0.0.1",
        param(
            "[::1]",
            marks=needs_ipv6,
        ),
    ],
)
def test_server_serves_wherever_localhost_leads(
    free_port: int,
    host: str,
    tmp_path: Path,
) -> None:
    """Assert that a server serves at every address that `localhost` can mean."""
    with Server(
        tmp_path,
        port=free_port,
    ):
        result = _get(f"http://{host}:{free_port}/").status

    expect = 200

    assert result == expect


@mark.parametrize(
    ("path", "body"),
    [
        param(
            "reload.js",
            b"{\n"
            b"  const src = new URL(document.currentScript.src);\n"
            b'  let served = Number(src.searchParams.get("reloads"));\n'
            b'  if (typeof SharedWorker === "function") {\n'
            b'    const url = new URL("/__crumpet__/worker.js", src);\n'
            b'    const port = new SharedWorker(url, "reloads").port;\n'
            b"    port.onmessage = () => location.reload();\n"
            b"    port.postMessage(served);\n"
            b'    addEventListener("pagehide", () => port.postMessage(false));\n'
            b'    addEventListener("pageshow", (event) => {\n'
            b"      if (event.persisted) port.postMessage(served);\n"
            b"    });\n"
            b"  } else {\n"
            b'    const url = new URL("/__crumpet__/events", src);\n'
            b"    const events = new EventSource(url);\n"
            b"    events.onmessage = (event) => {\n"
            b"      const reloads = Number(event.data);\n"
            b"      if (served < reloads) location.reload();\n"
            b"      else served = reloads;\n"
            b"    };\n"
            b"  }\n"
            b"}\n",
            id="reload",
        ),
        param(
            "worker.js",
            b"const pages = new Map();\n"
            b"let events = null;\n"
            b"let reloads = null;\n"
            b"onconnect = (event) => {\n"
            b"  const port = event.ports[0];\n"
            b"  port.onmessage = ({ data }) => {\n"
            b"    if (data === false) return pages.delete(port);\n"
            b"    if (reloads !== null) data = Math.min(data, reloads);\n"
            b"    pages.set(port, data);\n"
            b"    if (data < reloads) port.postMessage(null);\n"
            b"  };\n"
            b"  if (events) return;\n"
            b'  events = new EventSource("/__crumpet__/events");\n'
            b"  events.onerror = () => {\n"
            b"    reloads = null;\n"
            b"  };\n"
            b"  events.onmessage = (event) => {\n"
            b"    reloads = Number(event.data);\n"
            b"    for (const [port, served] of pages) {\n"
            b"      if (served < reloads) port.postMessage(null);\n"
            b"      else pages.set(port, reloads);\n"
            b"    }\n"
            b"  };\n"
            b"};\n",
            id="worker",
        ),
    ],
)
def test_server_serves_reload_scripts(
    body: bytes,
    free_port: int,
    path: str,
    tmp_path: Path,
) -> None:
    """Assert that a server serves the scripts that reload HTML files."""
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        response = _get(server.url + "__crumpet__/" + path)

    headers = dict(response.headers)

    result = (
        response.status,
        headers["Content-type"],
        headers["Cache-Control"],
        response.body,
    )

    expect = (
        200,
        "text/javascript",
        "no-store",
        body,
    )

    assert result == expect


def test_server_wait_raises_before_entering(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that waiting on a server before entering it raises `RuntimeError`."""
    server = Server(
        tmp_path,
        port=free_port,
    )

    with (
        closing(server),
        raises(RuntimeError) as ex,
    ):
        server.wait()

    result = str(ex.value)
    expect = "A server can't be waited on before it's entered"

    assert result == expect


@mark.usefixtures("watching_fails_after_start")
def test_server_wait_raises_when_watching_fails(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that `wait()` raises the error when watching fails after it starts."""
    with (
        Server(
            tmp_path,
            port=free_port,
            watch=True,
        ) as server,
        raises(OSError) as ex,
    ):
        server.wait()

    result = str(ex.value)
    expect = "OS file watch limit reached"

    assert result == expect


@mark.parametrize(
    "watch",
    [
        False,
        True,
    ],
)
def test_server_wait_returns_when_closed(
    free_port: int,
    tmp_path: Path,
    watch: bool,
) -> None:
    """
    Assert that `wait()` blocks until the server is closed, and returns once its port
    is free.
    """
    with Server(
        tmp_path,
        port=free_port,
        watch=watch,
    ) as server:
        waiter = Thread(target=server.wait)
        waiter.start()
        waiter.join(timeout=0.1)
        waiting = waiter.is_alive()

        closer = Thread(target=server.close)
        closer.start()
        waiter.join()

        # Before `close()` returns.
        with Server(
            tmp_path,
            port=free_port,
        ) as again:
            status = _get(again.url).status

        closer.join()

    result = (waiting, status)
    expect = (True, 200)

    assert result == expect


def test_server_url_names_port(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that a server's `url` names its port."""
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = server.url

    expect = f"http://localhost:{free_port}/"

    assert result == expect
