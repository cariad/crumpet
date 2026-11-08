import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from typing import cast

from notwork import PortInUseError, check_port_is_in_range

from .server import Server


def _error(message: str) -> int:
    """Print `message` as an error, and return the exit code for it."""
    print(
        f"crumpet: error: {message}",
        file=sys.stderr,
    )

    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the command line.

    Args:
        argv: Arguments, without the command's name. Defaults to `sys.argv[1:]`.

    Returns:
        Exit code.
    """
    parser = ArgumentParser(
        description="A cosy web server for local files.",
        prog="crumpet",
    )

    parser.add_argument(
        "directory",
        default=".",
        help="directory to serve (default: %(default)s)",
        metavar="DIRECTORY",
        nargs="?",
    )

    parser.add_argument(
        "--port",
        default=8000,
        help="port to serve on (default: %(default)s)",
        type=int,
    )

    try:
        args = parser.parse_args(argv)

        try:
            check_port_is_in_range(args.port)

        except ValueError as ex:
            parser.error(f"argument --port: {ex}")

    # `argparse` exits after `--help` and usage errors.
    except SystemExit as ex:
        return cast(int, ex.code)

    try:
        server = Server(
            args.directory,
            port=args.port,
            watch=True,
        )

    except FileNotFoundError:
        return _error(f"No such directory: '{args.directory}'")

    except NotADirectoryError:
        return _error(f"Not a directory: '{args.directory}'")

    except PortInUseError as ex:
        return _error(str(ex))

    try:
        with server:
            print(
                f"crumpet: Serving {args.directory!r} at {server.url}",
                file=sys.stderr,
            )

            print(
                "crumpet: Press Ctrl+C to stop.",
                file=sys.stderr,
            )

            server.wait()

    except KeyboardInterrupt:
        return 130

    except (OSError, RuntimeError) as ex:
        return _error(f"Can't watch '{args.directory}': {ex}")

    # Nothing closes the server while it waits.
    return 0  # pragma: no cover
