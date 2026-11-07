from collections.abc import Generator
from contextlib import closing, contextmanager
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

from notwork import PortInUseError, PortOutOfRangeError
from pytest import MonkeyPatch, mark, param, raises

from crumpet import Server
from tests.marks import needs_dual_stack, needs_ipv6
from tests.pages import fetch


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
        result = fetch(server.url).status

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
        result = fetch(server.url).status

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
        fetch(server.url)

    with (
        page,
        Server(
            tmp_path,
            port=free_port,
        ) as server,
    ):
        result = fetch(server.url).status

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
        result = fetch(server.url).status

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
            status = fetch(again.url).status

        closer.join()

    result = (waiting, status)
    expect = (True, 200)

    assert result == expect
