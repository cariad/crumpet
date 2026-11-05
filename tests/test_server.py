from collections.abc import Generator
from contextlib import closing, contextmanager
from errno import EADDRINUSE
from functools import partial
from http.client import HTTPConnection
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
    """
    Assert that creating a server on a busy port raises `OSError` with
    `EADDRINUSE`.
    """
    with (
        _listen(
            host,
            dual_stack=dual_stack,
            reuse_address=reuse_address,
        ) as port,
        raises(OSError) as ex,
    ):
        Server(
            tmp_path,
            port=port,
        )

    result = ex.value.errno
    expect = EADDRINUSE

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
        -1,
        65536,
    ],
)
def test_server_raises_for_port_out_of_range(
    port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that creating a server on a port that isn't 0-65535 raises
    `ValueError`.
    """
    with raises(ValueError) as ex:
        Server(
            tmp_path,
            port=port,
        )

    result = str(ex.value)
    expect = f"Port {port} must be 0-65535"

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
        # A directory without an index page is listed.
        "",
        "docs/",
        # A directory named without a trailing slash is redirected.
        "docs",
        # A directory serves its index page.
        "html/",
        "htm/",
        "notes.txt",
        "missing.txt",
        # Symlinks are followed wherever they lead.
        "linked.txt",
        "linked/",
    ],
)
def test_server_serves_like_python(
    free_port: int,
    path: str,
    served: Path,
) -> None:
    """
    Assert that a server serves files, index pages, listings, redirects and errors the
    way Python's own server does.
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
