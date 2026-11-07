from collections.abc import Iterable, Sized
from contextlib import ExitStack
from errno import ENOENT, ENOTDIR
from itertools import chain
from os import PathLike, fspath, strerror
from stat import S_ISDIR
from threading import Event, Thread
from typing import Self

import watchfiles
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
        watch: Whether to reload HTML pages when anything changes.

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

        if self._watch:
            # Watching yields when it's idle too, so that the first yield means it's
            # started, and no change after entering is missed.
            changes = watchfiles.watch(
                self._directory,
                rust_timeout=50,
                stop_event=self._stop_watching,
                watch_filter=None,
                yield_on_timeout=True,
            )

            try:
                first = next(changes)

            except BaseException:
                self.close()
                raise

            self._watcher = Thread(
                target=self._reload_after_changes,
                args=(chain([first], changes),),
                daemon=True,
            )

            self._watcher.start()

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
        watch: bool = False,
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
        self._directory = absolute
        self._port = port
        self._reloader = reloader
        self._served: list[Listener] = []
        self._stop_watching = Event()
        self._watch = watch
        self._watcher: Thread | None = None

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

            self._stop_watching.set()

            # Each takes up to half a second to stop, so stop them all at once.
            stoppers = [Thread(target=listener.shutdown) for listener in self._served]

            for stopper in stoppers:
                stopper.start()

            for stopper in stoppers:
                stopper.join()

            if self._watcher:
                self._watcher.join()

    def reload(self) -> None:
        """Reload every open HTML page."""
        self._reloader.reload()

    @property
    def url(self) -> str:
        """URL of the served directory."""
        return f"http://localhost:{self._port}/"

    def _reload_after_changes(
        self,
        changes: Iterable[Sized],
    ) -> None:
        """Reload every open HTML page after each burst of `changes`."""
        for burst in changes:
            # Empty when watching was idle.
            if burst:
                self._reloader.reload()
