from contextlib import suppress
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from io import BytesIO
from pathlib import Path
from socket import socket
from socketserver import BaseServer
from typing import TYPE_CHECKING, AnyStr, BinaryIO, cast
from urllib.parse import urlsplit

from ._reloader import Reloader

if TYPE_CHECKING:
    from _typeshed import SupportsRead, SupportsWrite

# Reserved, so files under it can't be served.
_PREFIX = "/__crumpet__/"

_EVENTS_PATH = _PREFIX + "events"
_SCRIPT_PATH = _PREFIX + "reload.js"
_WORKER_PATH = _PREFIX + "worker.js"

# Seconds between heartbeats, which find out when a page has gone.
_HEARTBEAT = 1

# Pages share one worker's event stream, since browsers allow only six connections
# to a server. Chrome on Android has no shared workers.
_SCRIPTS = {
    _SCRIPT_PATH: (
        'if (typeof SharedWorker === "function") {\n'
        f'  new SharedWorker("{_WORKER_PATH}")'
        ".port.onmessage = () => location.reload();\n"
        "} else {\n"
        f'  new EventSource("{_EVENTS_PATH}").onmessage = () => location.reload();\n'
        "}\n"
    ),
    # Every page reloads and connects again, so each reload forgets the old ones.
    _WORKER_PATH: (
        "const ports = [];\n"
        "onconnect = (event) => ports.push(event.ports[0]);\n"
        f'new EventSource("{_EVENTS_PATH}").onmessage = () => {{\n'
        "  for (const port of ports.splice(0)) port.postMessage(null);\n"
        "};\n"
    ),
}

_SCRIPT_TAG = f'<script src="{_SCRIPT_PATH}"></script>'.encode()


class Handler(SimpleHTTPRequestHandler):
    """
    Serves requests like Python's own server, adding the reload script to HTML files.

    Args:
        request: Connection to serve.
        client_address: Address of the client.
        server: Server that accepted the connection.
        directory: Absolute path to the directory to serve.
        reloader: Tells open pages when to reload.
    """

    def __init__(
        self,
        request: socket,
        client_address: object,
        server: BaseServer,
        *,
        directory: Path,
        reloader: Reloader,
    ) -> None:
        self._html = False
        self._ok = False
        self._reloader = reloader

        super().__init__(
            request,
            client_address,
            server,
            directory=directory,
        )

    def copyfile(
        self,
        source: SupportsRead[AnyStr],
        outputfile: SupportsWrite[AnyStr],
    ) -> None:
        """Copy `source` to `outputfile`, then the reload script for an HTML body."""
        super().copyfile(source, outputfile)

        if self._html:
            outputfile.write(cast("AnyStr", _SCRIPT_TAG))

    def do_GET(self) -> None:
        """Serve a GET request."""
        if urlsplit(self.path).path == _EVENTS_PATH:
            self._send_events()
        else:
            super().do_GET()

    def send_head(self) -> BinaryIO | None:
        """Send the response's status and headers, and return its body."""
        path = urlsplit(self.path).path

        if script := _SCRIPTS.get(path):
            body = script.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-type", "text/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return BytesIO(body)

        if self._reserved():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        # Modification times are only precise to the second, so a file rewritten
        # within one could be reported unchanged.
        del self.headers["If-Modified-Since"]

        return super().send_head()

    def send_header(self, keyword: str, value: str) -> None:
        """Send a header, counting the reload script in an HTML body's length."""
        keyword_lower = keyword.lower()

        # Errors write their HTML bodies directly, without the script.
        if self._ok and keyword_lower == "content-type":
            self._html = value.partition(";")[0].strip().lower() == "text/html"

        if self._html and keyword_lower == "content-length":
            value = str(int(value) + len(_SCRIPT_TAG))

        super().send_header(keyword, value)

    def send_response(self, code: int, message: str | None = None) -> None:
        """Send the response's status."""
        self._html = False
        self._ok = code == HTTPStatus.OK
        super().send_response(code, message)

    def _reserved(self) -> bool:
        """Whether the request's file is under the reserved directory."""
        # Compared as files, since paths can reach the same one by other case or links.
        reserved = Path(self.directory, _PREFIX.strip("/"))
        path = Path(self.translate_path(self.path)).resolve()

        # A missing file can't be served anyway.
        with suppress(OSError):
            return any(p.samefile(reserved) for p in (path, *path.parents))

        return False

    def _send_events(self) -> None:
        """Send an event each time pages reload, until the server stops."""
        seen = self._reloader.reloads

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", "text/event-stream")
        self.end_headers()

        # The page has gone.
        with suppress(OSError):
            while (reloads := self._reloader.wait(seen, _HEARTBEAT)) is not None:
                self.wfile.write(b"data: reload\n\n" if reloads != seen else b":\n\n")
                seen = reloads
