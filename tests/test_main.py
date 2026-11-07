import sys
from collections.abc import Callable, Generator
from pathlib import Path
from runpy import run_module
from signal import SIGINT
from subprocess import PIPE, Popen
from threading import Timer
from typing import IO, cast
from urllib.request import urlopen

from pytest import CaptureFixture, MonkeyPatch, mark, raises

from crumpet import Server
from crumpet._main import main
from tests.marks import posix_only, windows_only
from tests.pages import next_event, open_page, settle


def test_main_prints_help(capsys: CaptureFixture[str]) -> None:
    """Assert that `--help` prints the command's help to stdout."""
    exit_code = main(
        [
            "--help",
        ],
    )

    assert exit_code == 0

    result = capsys.readouterr()

    expect = (
        "usage: crumpet [-h] [--port PORT] [DIRECTORY]\n"
        "\n"
        "Cosy local HTTP hosting.\n"
        "\n"
        "positional arguments:\n"
        "  DIRECTORY    directory to serve (default: .)\n"
        "\n"
        "options:\n"
        "  -h, --help   show this help message and exit\n"
        "  --port PORT  port to serve on (default: 8000)\n",
        "",
    )

    assert result == expect


@posix_only
def test_main_raises_for_symlink_loop(tmp_path: Path) -> None:
    """
    Assert that a directory that can't be followed raises its error, rather than being
    reported as a port that can't be claimed.
    """
    directory = tmp_path / "loop"
    directory.symlink_to("loop")

    with raises(OSError) as ex:
        main(
            [
                str(directory),
            ],
        )

    result = ex.value.filename
    expect = str(directory)

    assert result == expect


def test_main_reloads_after_change(
    free_port: int,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Assert that the command reloads every open page after a change."""
    events: list[bytes] = []

    def change_then_press_ctrl_c(_: Server) -> None:
        with open_page(f"http://localhost:{free_port}/") as page:
            settle(page)
            (tmp_path / "index.html").write_text("Built")
            events.append(next_event(page))

        raise KeyboardInterrupt

    monkeypatch.setattr(
        Server,
        "wait",
        change_then_press_ctrl_c,
    )

    exit_code = main(
        [
            str(tmp_path),
            "--port",
            str(free_port),
        ],
    )

    assert exit_code == 130

    result = events
    expect = [b"data: reload\n"]

    assert result == expect


def test_main_reports_busy_port(
    busy_port: int,
    capsys: CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Assert that a busy port is reported."""
    exit_code = main(
        [
            str(tmp_path),
            "--port",
            str(busy_port),
        ],
    )

    assert exit_code == 1

    result = capsys.readouterr()

    expect = (
        "",
        f"crumpet: error: Port {busy_port} is in use\n",
    )

    assert result == expect


def test_main_reports_missing_directory(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Assert that a directory that doesn't exist is reported as it was given."""
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "buld",
        ],
    )

    assert exit_code == 1

    result = capsys.readouterr()

    expect = (
        "",
        "crumpet: error: No such directory: 'buld'\n",
    )

    assert result == expect


def test_main_reports_non_directory(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Assert that a path that isn't a directory is reported as it was given."""
    (tmp_path / "notes.txt").touch()
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "notes.txt",
        ],
    )

    assert exit_code == 1

    result = capsys.readouterr()

    expect = (
        "",
        "crumpet: error: Not a directory: 'notes.txt'\n",
    )

    assert result == expect


@mark.parametrize(
    (
        "port",
        "message",
    ),
    [
        (
            "eighty",
            "invalid int value: 'eighty'",
        ),
        (
            "0",
            "Port 0 must be 1-65535",
        ),
        (
            "65536",
            "Port 65536 must be 1-65535",
        ),
    ],
)
def test_main_reports_port_usage_error(
    capsys: CaptureFixture[str],
    message: str,
    port: str,
) -> None:
    """Assert that a port that isn't a number from 1 to 65535 is a usage error."""
    exit_code = main(
        [
            "--port",
            port,
        ],
    )

    assert exit_code == 2

    result = capsys.readouterr()

    expect = (
        "",
        "usage: crumpet [-h] [--port PORT] [DIRECTORY]\n"
        f"crumpet: error: argument --port: {message}\n",
    )

    assert result == expect


def test_main_reports_watch_failure(
    capsys: CaptureFixture[str],
    free_port: int,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Assert that a directory that can't be watched is reported as it was given."""
    (tmp_path / "build").mkdir()
    monkeypatch.chdir(tmp_path)

    def run_out_of_watches(*_: object, **__: object) -> Generator[set[object]]:
        raise OSError("OS file watch limit reached")
        yield set()

    monkeypatch.setattr(
        "crumpet.server.watchfiles.watch",
        run_out_of_watches,
    )

    exit_code = main(
        [
            "build",
            "--port",
            str(free_port),
        ],
    )

    assert exit_code == 1

    result = capsys.readouterr()

    expect = (
        "",
        "crumpet: error: Can't watch 'build': OS file watch limit reached\n",
    )

    assert result == expect


@mark.parametrize(
    "watching_fails_after_start",
    [
        OSError("OS file watch limit reached"),
        RuntimeError("watch thread unexpectedly received a signal"),
    ],
    indirect=True,
)
def test_main_reports_watch_failure_after_start(
    capsys: CaptureFixture[str],
    free_port: int,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    watching_fails_after_start: Exception,
) -> None:
    """
    Assert that watching that fails after it starts is reported, and that the command
    stops serving and frees the port.
    """
    (tmp_path / "build").mkdir()
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "build",
            "--port",
            str(free_port),
        ],
    )

    assert exit_code == 1

    result = capsys.readouterr()

    expect = (
        "",
        f"crumpet: Serving 'build' at http://localhost:{free_port}/\n"
        "crumpet: Press Ctrl+C to stop.\n"
        f"crumpet: error: Can't watch 'build': {watching_fails_after_start}\n",
    )

    assert result == expect

    # Raises if the port is still claimed.
    Server(
        tmp_path,
        port=free_port,
    ).close()


def test_main_serves_until_ctrl_c(
    capsys: CaptureFixture[str],
    free_port: int,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Assert that the command serves its directory until Ctrl+C, then frees the port and
    exits with 130, printing nothing more.
    """
    (tmp_path / "build").mkdir()
    monkeypatch.chdir(tmp_path)

    printed: list[tuple[str, str]] = []
    statuses: list[int] = []

    def press_ctrl_c(_: Server) -> None:
        printed.append(capsys.readouterr())

        with urlopen(
            f"http://localhost:{free_port}/",
            timeout=5,
        ) as response:
            statuses.append(response.status)

            # Closing with the body unread can reset the connection mid-response.
            response.read()

        # Python's server logs the request.
        capsys.readouterr()

        raise KeyboardInterrupt

    monkeypatch.setattr(
        Server,
        "wait",
        press_ctrl_c,
    )

    exit_code = main(
        [
            "build",
            "--port",
            str(free_port),
        ],
    )

    assert exit_code == 130

    result = printed

    expect = [
        (
            "",
            f"crumpet: Serving 'build' at http://localhost:{free_port}/\n"
            "crumpet: Press Ctrl+C to stop.\n",
        ),
    ]

    assert result == expect

    result = statuses
    expect = [200]

    assert result == expect

    result = capsys.readouterr()

    expect = (
        "",
        "",
    )

    assert result == expect

    # Raises if the port is still claimed.
    Server(
        tmp_path,
        port=free_port,
    ).close()


_RUN_THEN_PRESS_CTRL_C_ON_WINDOWS = """
import ctypes
import sys
import threading

from crumpet._main import main

kernel32 = ctypes.windll.kernel32

# Handle Ctrl+C even if the test runner ignores it.
kernel32.SetConsoleCtrlHandler(None, False)

def press_ctrl_c():
    sys.stdin.read()

    # Every process on this console, which is only this one.
    kernel32.GenerateConsoleCtrlEvent(0, 0)

threading.Thread(target=press_ctrl_c, daemon=True).start()
sys.exit(main())
"""
"""Runs the command, and presses Ctrl+C once stdin closes."""


def _assert_ctrl_c_stops(
    command: list[str],
    free_port: int,
    tmp_path: Path,
    press_ctrl_c: Callable[[Popen[str]], None],
    creationflags: int = 0,
) -> None:
    """
    Assert that `press_ctrl_c` stops `command` while it waits, then frees the port and
    exits with 130, printing nothing more.
    """
    (tmp_path / "build").mkdir()

    with Popen(
        [
            sys.executable,
            *command,
            "build",
            "--port",
            str(free_port),
        ],
        creationflags=creationflags,
        cwd=tmp_path,
        stderr=PIPE,
        stdin=PIPE,
        stdout=PIPE,
        text=True,
    ) as process:
        # Kills a stuck command, so that reading from it can't block forever.
        timer = Timer(30, process.kill)
        timer.start()

        try:
            stderr = cast(IO[str], process.stderr)
            started = stderr.readline() + stderr.readline()
            press_ctrl_c(process)
            stdout, stopped = process.communicate()

        finally:
            timer.cancel()

    exit_code = process.returncode

    assert exit_code == 130

    result = (stdout, started + stopped)

    expect = (
        "",
        f"crumpet: Serving 'build' at http://localhost:{free_port}/\n"
        "crumpet: Press Ctrl+C to stop.\n",
    )

    assert result == expect

    # Raises if the port is still claimed.
    Server(
        tmp_path,
        port=free_port,
    ).close()


@posix_only
def test_main_stops_on_ctrl_c_on_posix(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that a real Ctrl+C stops the command while it waits, on POSIX."""
    _assert_ctrl_c_stops(
        ["-m", "crumpet"],
        free_port,
        tmp_path,
        lambda process: process.send_signal(SIGINT),
    )


@windows_only
def test_main_stops_on_ctrl_c_on_windows(
    free_port: int,
    tmp_path: Path,
) -> None:
    """Assert that a real Ctrl+C stops the command while it waits, on Windows."""
    # Lets type checkers on other systems see Windows-only names.
    assert sys.platform == "win32"

    from subprocess import CREATE_NEW_CONSOLE

    _assert_ctrl_c_stops(
        ["-c", _RUN_THEN_PRESS_CTRL_C_ON_WINDOWS],
        free_port,
        tmp_path,
        lambda process: cast(IO[str], process.stdin).close(),
        creationflags=CREATE_NEW_CONSOLE,
    )


def test_python_m_crumpet_calls_itself_crumpet(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    """Assert that `python -m crumpet` calls itself `crumpet`."""
    # `run_module` replaces the first argument with the module's path, as Python does.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "-m",
            "--port",
            "0",
        ],
    )

    with raises(SystemExit) as ex:
        run_module(
            "crumpet",
            alter_sys=True,
            run_name="__main__",
        )

    exit_code = ex.value.code

    assert exit_code == 2

    result = capsys.readouterr()

    expect = (
        "",
        "usage: crumpet [-h] [--port PORT] [DIRECTORY]\n"
        "crumpet: error: argument --port: Port 0 must be 1-65535\n",
    )

    assert result == expect
