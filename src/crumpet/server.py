from contextlib import ExitStack
from errno import ENOENT, ENOTDIR
from os import PathLike, fspath, strerror
from stat import S_ISDIR
from threading import Thread
from typing import Self

from rootle import resolve, status

from ._claim_port import claim_port
from ._listener import Listener


class Server:
    """
    Serves a directory on `localhost`.

    Args:
        directory: Path to the directory to serve.
        port: Port to serve on.

    Raises:
        FileNotFoundError: When `directory` doesn't exist.
        NotADirectoryError: When `directory` isn't a directory.
        OSError:
            - With `errno.EADDRINUSE` when the `port` is in use.
            - When the path to `directory` can't be followed.
            - When `port` can't be used for any other reason.
        ValueError: When `port` isn't between 0 and 65535.

    Examples:
        ```python
        with Server("build", port=8000) as server:
            print(server.url)  # http://localhost:8000/
        ```
    """

    def __enter__(self) -> Self:
        """
        Start serving.

        Raises:
            RuntimeError: When the server has already been entered.
        """
        if self._served:
            raise RuntimeError("A server can only be entered once")

        self._served = self._listeners

        for listener in self._served:
            Thread(
                target=listener.serve_forever,
                daemon=True,
            ).start()

        return self

    def __exit__(self, *_: object) -> None:
        """Stop serving, and free the port."""
        with ExitStack() as stack:
            for listener in self._listeners:
                stack.callback(listener.server_close)

            # Each takes up to half a second to stop, so stop them all at once.
            stoppers = [Thread(target=listener.shutdown) for listener in self._served]

            for stopper in stoppers:
                stopper.start()

            for stopper in stoppers:
                stopper.join()

    def __init__(
        self,
        directory: PathLike[str] | str,
        *,
        port: int,
    ) -> None:
        directory_status = status(directory)

        if directory_status is None:
            raise FileNotFoundError(
                ENOENT,
                strerror(ENOENT),
                fspath(directory),
            )

        if not S_ISDIR(directory_status.st_mode):
            raise NotADirectoryError(
                ENOTDIR,
                strerror(ENOTDIR),
                fspath(directory),
            )

        if not 0 <= port <= 65535:
            raise ValueError(f"Port {port} must be 0-65535")

        absolute = resolve(directory)

        with ExitStack() as stack:
            sockets = [stack.enter_context(sock) for sock in claim_port(port)]

            self._listeners = [
                Listener(
                    sock,
                    absolute,
                )
                for sock in sockets
            ]

            # The listeners close the sockets now.
            stack.pop_all()

        self._port = port
        self._served: list[Listener] = []

    @property
    def url(self) -> str:
        """URL of the served directory."""
        return f"http://localhost:{self._port}/"
