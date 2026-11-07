from os import fsdecode
from pathlib import Path

from pytest import mark, raises

from crumpet import Server
from tests.marks import linux_only, posix_only, windows_only
from tests.pages import fetch


@mark.parametrize(
    (
        "path",
        "quoted",
    ),
    [
        (
            "notes.txt",
            "notes.txt",
        ),
        (
            "my page.html",
            "my%20page.html",
        ),
        (
            "docs/café.html",
            "docs/caf%C3%A9.html",
        ),
        (
            Path("docs/guide.txt"),
            "docs/guide.txt",
        ),
        (
            "missing.txt",
            "missing.txt",
        ),
        (
            "docs/../notes.txt",
            "notes.txt",
        ),
        # `linked` leads outside the directory, but isn't followed.
        (
            "linked/../notes.txt",
            "notes.txt",
        ),
        (
            "docs/",
            "docs/",
        ),
        (
            "docs/sub/..",
            "docs/",
        ),
        (
            "",
            "",
        ),
        (
            ".",
            "",
        ),
    ],
)
def test_server_url_for(
    free_port: int,
    path: Path | str,
    quoted: str,
    served: Path,
) -> None:
    """Assert that `url_for()` gives the URL of a path within the served directory."""
    with Server(
        served,
        port=free_port,
    ) as server:
        result = server.url_for(path)

    expect = server.url + quoted

    assert result == expect


@posix_only
def test_server_url_for_keeps_backslashes_on_posix(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that `url_for()` keeps a backslash in a name on Linux and macOS."""
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = server.url_for("docs\\guide.txt")

    expect = server.url + "docs%5Cguide.txt"

    assert result == expect


@mark.parametrize(
    (
        "path",
        "expect",
    ),
    [
        (
            "/notes.txt",
            "Path '/notes.txt' must be relative",
        ),
        (
            "..",
            "Path '..' leads outside the directory",
        ),
        (
            "docs/../../served/notes.txt",
            "Path 'docs/../../served/notes.txt' leads outside the directory",
        ),
    ],
)
def test_server_url_for_raises(
    expect: str,
    free_port: int,
    path: str,
    served: Path,
) -> None:
    """
    Assert that `url_for()` raises `ValueError` for a path that isn't relative, or
    leads outside the served directory.
    """
    with (
        Server(
            served,
            port=free_port,
        ) as server,
        raises(ValueError) as ex,
    ):
        server.url_for(path)

    result = str(ex.value)

    assert result == expect


@windows_only
@mark.parametrize(
    "path",
    [
        "C:notes.txt",
        "C:\\notes.txt",
        "\\notes.txt",
        "\\\\server\\share\\notes.txt",
    ],
)
def test_server_url_for_raises_for_windows_drives(
    free_port: int,
    path: str,
    tmp_path: Path,
) -> None:
    """Assert that `url_for()` raises `ValueError` for a path with a drive or a root."""
    with (
        Server(
            tmp_path,
            port=free_port,
        ) as server,
        raises(ValueError) as ex,
    ):
        server.url_for(path)

    result = str(ex.value)
    expect = f"Path {path!r} must be relative"

    assert result == expect


@windows_only
@mark.parametrize(
    (
        "path",
        "quoted",
    ),
    [
        (
            "docs\\guide.txt",
            "docs/guide.txt",
        ),
        (
            "docs\\",
            "docs/",
        ),
    ],
)
def test_server_url_for_separates_backslashes_on_windows(
    free_port: int,
    path: str,
    quoted: str,
    tmp_path: Path,
) -> None:
    """Assert that `url_for()` separates names with backslashes on Windows."""
    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = server.url_for(path)

    expect = server.url + quoted

    assert result == expect


@mark.parametrize(
    "name",
    [
        "my notes.txt",
        "café.txt",
        "#1 100%.txt",
    ],
)
def test_server_url_for_serves_file(
    free_port: int,
    name: str,
    tmp_path: Path,
) -> None:
    """Assert that the server serves the file that `url_for()` names."""
    (tmp_path / name).write_text("Notes")

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = fetch(server.url_for(name)).body

    expect = b"Notes"

    assert result == expect


@linux_only
def test_server_url_for_serves_file_with_non_utf8_name(
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that the server serves the file that `url_for()` names, when its name isn't
    valid UTF-8.
    """
    name = fsdecode(b"\xff.txt")
    (tmp_path / name).write_text("Notes")

    with Server(
        tmp_path,
        port=free_port,
    ) as server:
        result = fetch(server.url_for(name)).body

    expect = b"Notes"

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
