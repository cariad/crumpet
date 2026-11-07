from collections.abc import Generator, Iterable
from contextlib import ExitStack, closing, suppress
from errno import ENOENT, ENOTDIR
from itertools import chain
from os import PathLike, fspath, stat, stat_result, strerror
from os.path import samestat
from pathlib import Path
from stat import S_ISDIR
from sys import platform
from threading import Event, Thread
from typing import Self

import watchfiles
from notwork import claim_port
from rootle import resolve, status
from watchfiles import Change

from ._listener import Listener
from ._reloader import Reloader

# Milliseconds between checks for changes and for a replaced directory.
_INTERVAL = 50


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
            OSError: When watching can't start.
            RuntimeError: When the server has already been entered or closed.
        """
        if self._served:
            raise RuntimeError("A server can only be entered once")

        if self._closed:
            raise RuntimeError("A closed server can't be entered")

        if self._watch:
            # The first yield means that watching has started, so no change after
            # entering is missed.
            changes = self._changes()

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
        self._wake = Event()
        self._watch = watch
        self._watch_error: Exception | None = None
        self._watcher: Thread | None = None

    def close(self) -> None:
        """Stop serving, and free the port."""
        if self._closed:
            return

        self._closed = True

        with ExitStack() as stack:
            # Runs last, so `wait()` returns once the port is free.
            stack.callback(self._wake.set)
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

    def wait(self) -> None:
        """
        Wait until the server is closed, or watching fails.

        Raises:
            OSError: When watching fails.
            RuntimeError:
                - When watching fails.
                - When the server hasn't been entered.
        """
        if not self._served and not self._closed:
            raise RuntimeError("A server can't be waited on before it's entered")

        # In steps, since a signal delivered to another thread can't interrupt an
        # endless wait.
        while not self._wake.wait(_INTERVAL / 1000):
            pass

        if self._watch_error is not None:
            # Without a traceback, so that raising again doesn't extend it.
            raise self._watch_error.with_traceback(None)

    def _changes(self) -> Generator[bool]:
        """
        Yield whether anything changed each time watching yields, until stopped.

        Watches again whenever the directory is replaced, since a watch on Linux and
        Windows ends with the directory that it started on.
        """
        replaced = False
        watched = _status(self._directory)

        while True:
            try:
                # Watching yields when it's idle too, so that a replacement is noticed.
                with closing(
                    watchfiles.watch(
                        self._directory,
                        rust_timeout=_INTERVAL,
                        stop_event=self._stop_watching,
                        watch_filter=None,
                        yield_on_timeout=True,
                    ),
                ) as changes:
                    for burst in changes:
                        current = _status(self._directory)
                        same = watched and current and samestat(watched, current)

                        # Linux can give a new directory the old one's inode. macOS
                        # can report a deletion from before watching started.
                        deleted = (
                            platform == "linux"
                            and (Change.deleted, str(self._directory)) in burst
                        )

                        replacing = not same or deleted

                        # A replacement that's already present is reported once
                        # watching it starts.
                        if not (replacing and current):
                            # Changes made before watching again were missed.
                            yield bool(burst) or replaced
                            replaced = False

                        if replacing:
                            break

                    else:
                        return

            # The directory can go again before watching it again starts.
            except OSError:
                if not replaced:
                    raise

                if self._stop_watching.wait(_INTERVAL / 1000):  # pragma: no cover
                    return

            replaced = True

            while (watched := _status(self._directory)) is None:
                if self._stop_watching.wait(_INTERVAL / 1000):
                    return

    def _reload_after_changes(
        self,
        changes: Iterable[bool],
    ) -> None:
        """
        Reload every open HTML page each time `changes` yields that something did, until
        watching stops or fails.
        """
        try:
            for changed in changes:
                if changed:
                    self._reloader.reload()

        except Exception as ex:
            self._watch_error = ex
            self._wake.set()


def _status(path: Path) -> stat_result | None:
    """
    Get the status of `path`, or `None` on any error.

    Unlike `status`, this never raises, since Windows denies access to a directory
    that's being deleted, and raising would stop watching.
    """
    with suppress(OSError):
        return stat(path)

    return None
