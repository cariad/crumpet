from collections.abc import Callable
from functools import partial
from os import utime
from pathlib import Path
from shutil import rmtree

from pytest import MonkeyPatch, mark

from crumpet import Server
from tests.pages import fetch, next_event, open_page, settle


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


@mark.parametrize(
    "directory",
    [
        # The served directory is the symlink.
        "link",
        # A symlink leads to the served directory's parent.
        "link/site",
    ],
)
def test_server_reloads_after_symlink_leads_elsewhere(
    directory: str,
    free_port: int,
    tmp_path: Path,
) -> None:
    """
    Assert that a watching server reloads open pages when a symlink on the path to its
    directory is changed to lead elsewhere, then keeps reloading them after changes
    there.
    """
    for name in ("before", "after"):
        (tmp_path / name / "site").mkdir(parents=True)

    link = tmp_path / "link"
    link.symlink_to(tmp_path / "before")

    with (
        Server(
            tmp_path / directory,
            port=free_port,
            watch=True,
        ) as server,
        open_page(server.url) as page,
    ):
        settle(page)

        link.unlink()
        link.symlink_to(tmp_path / "after")
        retargeted = next_event(page)
        settle(page)

        (tmp_path / directory / "index.html").write_text("Built")
        changed = next_event(page)

    result = [
        retargeted,
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

        result = fetch(server.url + path).status

    expect = 404

    assert result == expect
