from contextlib import ExitStack
from errno import ENOENT, ENOTDIR
from os import PathLike, fspath, strerror
from stat import S_ISDIR
from threading import Thread
from typing import Self

from notwork import claim_port
from rootle import resolve, status

from ._listener import Listener
from ._reloader import Reloader


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
            - When the path to `directory` can't be followed.
            - When `port` can't be claimed for any other reason.
        PortInUseError: When `port` is in use.
        PortOutOfRangeError: When `port` isn't in the valid range.
        PortTypeError: When `port` isn't an `int`.

    Examples:
        ```python
        with Server("build", port=8000) as server:
            print(server.url)  # http://localhost:8000/
            server.reload()    # Reload every open HTML page
        ```
    """

    def __enter__(self) -> Self:
        """
        Start serving.

        Raises:
            RuntimeError: When the server has already been entered or closed.
        """
        if self._served:
            raise RuntimeError("A server can only be entered once")

        if self._closed:
            raise RuntimeError("A closed server can't be entered")

        self._served = self._listeners

        for listener in self._served:
            Thread(
                target=listener.serve_forever,
                daemon=True,
            ).start()

        return self

    def __exit__(self, *_: object) -> None:
        """Stop serving, and free the port."""
        self.close()

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

        absolute = resolve(directory)

        claimed = claim_port(port)
        reloader = Reloader()

        with ExitStack() as stack:
            stack.enter_context(claimed)

            self._listeners = [
                Listener(
                    sock,
                    absolute,
                    reloader,
                )
                for sock in claimed.sockets
            ]

            stack.pop_all()

        self._claimed = claimed
        self._closed = False
        self._port = port
        self._reloader = reloader
        self._served: list[Listener] = []

    def close(self) -> None:
        """Stop serving, and free the port."""
        if self._closed:
            return

        self._closed = True

        with ExitStack() as stack:
            stack.callback(self._claimed.close)
            stack.callback(self._reloader.close)

            for listener in self._listeners:
                stack.callback(listener.server_close)

            # Each takes up to half a second to stop, so stop them all at once.
            stoppers = [Thread(target=listener.shutdown) for listener in self._served]

            for stopper in stoppers:
                stopper.start()

            for stopper in stoppers:
                stopper.join()

    def reload(self) -> None:
        """Reload every open HTML page."""
        self._reloader.reload()

    @property
    def url(self) -> str:
        """URL of the served directory."""
        return f"http://localhost:{self._port}/"
