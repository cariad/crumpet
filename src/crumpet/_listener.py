from contextlib import suppress
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from socket import SHUT_RDWR, getfqdn, socket
from typing import Any
from weakref import WeakSet

from ._handler import Handler
from ._reloader import Reloader


class Listener(ThreadingHTTPServer):
    """
    Serves a directory on a listening socket.

    Args:
        sock: Claimed socket to serve on.
        directory: Absolute path to the directory to serve.
        reloader: Tells open pages when to reload.
    """

    def __init__(
        self,
        sock: socket,
        directory: Path,
        reloader: Reloader,
    ) -> None:
        self.address_family = sock.family

        super().__init__(
            sock.getsockname(),
            partial(
                Handler,
                directory=directory,
                reloader=reloader,
            ),
            bind_and_activate=False,
        )

        self._connections: WeakSet[socket] = WeakSet()

        # `TCPServer` always makes a socket of its own, so swap it for the one that's
        # claimed.
        self.socket.close()
        self.socket = sock

        # Name the server as `HTTPServer.server_bind` would, without binding again.
        host, port = sock.getsockname()[:2]
        self.server_name = getfqdn(host)
        self.server_port = port

        self.server_activate()

    def get_request(self) -> tuple[socket, Any]:
        """Accept a connection."""
        connection, address = super().get_request()
        self._connections.add(connection)
        return connection, address

    def server_close(self) -> None:
        """Stop listening, and shut down open connections."""
        super().server_close()

        # Closing doesn't wait for request threads, so they'd carry on serving.
        for connection in list(self._connections):
            with suppress(OSError):
                connection.shutdown(SHUT_RDWR)
