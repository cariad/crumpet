from collections.abc import Generator
from contextlib import contextmanager
from errno import EADDRINUSE
from pathlib import Path
from socket import (
    AF_INET,
    AF_INET6,
    IPPROTO_IPV6,
    IPV6_V6ONLY,
    SO_REUSEADDR,
    SOCK_STREAM,
    SOL_SOCKET,
    socket,
)

from pytest import MonkeyPatch, fixture, mark, param, raises

from crumpet import Server
from tests.marks import needs_dual_stack, needs_ipv6


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


@fixture
def free_port() -> int:
    """A port that nothing listens on."""
    with socket(AF_INET, SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]

    return port


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


def test_server_raises_not_implemented(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that creating a valid server raises `NotImplementedError`, because serving
    isn't implemented yet.
    """
    with raises(NotImplementedError):
        Server(
            tmp_path,
            port=free_port,
        )
