import sys
from pathlib import Path
from runpy import run_module
from urllib.request import urlopen

from pytest import CaptureFixture, MonkeyPatch, mark, raises

from crumpet import Server
from crumpet._main import main
from tests.marks import posix_only


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

    def press_ctrl_c(_: float) -> None:
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
        "crumpet._main.sleep",
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
