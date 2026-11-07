from collections.abc import Generator
from contextlib import closing, contextmanager
from functools import partial
from http.client import HTTPConnection, HTTPResponse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
from threading import Thread
from typing import NamedTuple
from urllib.parse import urlsplit

from notwork import PortInUseError, PortOutOfRangeError
from pytest import MonkeyPatch, mark, param, raises

from crumpet import Server
from tests.marks import needs_dual_stack, needs_ipv6


class _Response(NamedTuple):
    """A response, without its date."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes


def _get(url: str) -> _Response:
    """Get `url`, without following a redirect."""
    parts = urlsplit(url)

    with closing(
        HTTPConnection(
            parts.netloc,
            timeout=5,
        ),
    ) as connection:
        connection.request(
            "GET",
            parts.path,
        )

        response = connection.getresponse()

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
def _open_page(url: str) -> Generator[HTTPResponse]:
    """Connect to the server at `url` as an open page does, and yield its events."""
    parts = urlsplit(url)

    with closing(
        HTTPConnection(
            parts.netloc,
            timeout=5,
        ),
    ) as connection:
        connection.request(
            "GET",
            "/__crumpet__/events",
        )

        yield connection.getresponse()


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
    script = b'<script src="/__crumpet__/reload.js"></script>'

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
        ],
        body=python.body + script,
    )

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


def test_server_reload_reloads_open_pages(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that `reload()` tells every open page to reload."""
    with (
        Server(
            tmp_path,
            port=free_port,
        ) as server,
        _open_page(server.url) as first,
        _open_page(server.url) as second,
    ):
        server.reload()

        result = [
            first.readline(),
            second.readline(),
        ]

    expect = [
        b"data: reload\n",
        b"data: reload\n",
    ]

    assert result == expect


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
        expect = _get(python_url + path)

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
            b'if (typeof SharedWorker === "function") {\n'
            b'  const port = new SharedWorker("/__crumpet__/worker.js").port;\n'
            b"  port.onmessage = () => location.reload();\n"
            b'  addEventListener("pagehide", () => port.postMessage(false));\n'
            b'  addEventListener("pageshow", () => port.postMessage(true));\n'
            b"} else {\n"
            b'  new EventSource("/__crumpet__/events")'
            b".onmessage = () => location.reload();\n"
            b"}\n",
            id="reload",
        ),
        param(
            "worker.js",
            b"const ports = new Set();\n"
            b"onconnect = (event) => {\n"
            b"  const port = event.ports[0];\n"
            b"  ports.add(port);\n"
            b"  port.onmessage = (message) => {\n"
            b"    if (message.data) ports.add(port);\n"
            b"    else ports.delete(port);\n"
            b"  };\n"
            b"};\n"
            b'new EventSource("/__crumpet__/events").onmessage = () => {\n'
            b"  for (const port of ports) port.postMessage(null);\n"
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

    result = (
        response.status,
        dict(response.headers)["Content-type"],
        response.body,
    )

    expect = (
        200,
        "text/javascript",
        body,
    )

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
