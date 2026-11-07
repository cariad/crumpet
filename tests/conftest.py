from collections.abc import Generator
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket

from pytest import FixtureRequest, MonkeyPatch, fixture


@fixture
def busy_port() -> Generator[int]:
    """A port that another program listens on at 127.0.0.1."""
    with socket(AF_INET, SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()

        yield sock.getsockname()[1]


@fixture
def free_port() -> int:
    """A port that nothing listens on."""
    with socket(AF_INET, SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]

    return port


@fixture
def watching_fails_after_start(
    monkeypatch: MonkeyPatch,
    request: FixtureRequest,
) -> Exception:
    """
    Make watching start, then fail with the error that the test parametrizes, or an
    `OSError`.
    """
    error: Exception = getattr(
        request,
        "param",
        OSError("OS file watch limit reached"),
    )

    def start_then_fail(
        *_: object,
        **__: object,
    ) -> Generator[set[object]]:
        yield set()
        raise error

    monkeypatch.setattr(
        "crumpet.server.watchfiles.watch",
        start_then_fail,
    )

    return error


@fixture
def served(tmp_path: Path) -> Path:
    """
    A directory to serve.

    It holds:

    - `notes.txt`
    - `docs/`, without an index page
    - `linked.txt`, a symlink to a file outside the directory
    - `linked/`, a symlink to a directory outside the directory
    """
    outside = tmp_path / "outside"
    outside.mkdir()

    (outside / "elsewhere.txt").write_text("Elsewhere")

    directory = tmp_path / "served"
    directory.mkdir()

    (directory / "notes.txt").write_text("Notes")
    (directory / "docs").mkdir()
    (directory / "docs" / "guide.txt").write_text("Guide")
    (directory / "linked.txt").symlink_to(outside / "elsewhere.txt")

    (directory / "linked").symlink_to(
        outside,
        target_is_directory=True,
    )

    return directory
