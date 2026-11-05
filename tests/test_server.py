from pathlib import Path

from pytest import MonkeyPatch, mark, raises

from crumpet import Server


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


def test_server_raises_not_implemented_for_directory(tmp_path: Path) -> None:
    """
    Assert that creating a server for a directory raises `NotImplementedError`,
    because serving isn't implemented yet.
    """
    with raises(NotImplementedError):
        Server(
            tmp_path,
            port=8000,
        )
