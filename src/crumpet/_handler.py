from contextlib import suppress
from html import escape
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
#
# A page's script names the reloads before it was served, and the stream starts with
# the reloads so far, so a page that was still loading reloads if it missed one.
#
# A restarted server counts from 0 again, so a page counted past the stream is taken
# as up to date.
_SCRIPTS = {
    # URLs are relative to the script, since a page's <base> can lead elsewhere.
    #
    # The worker is named apart from ones that older versions left running.
    _SCRIPT_PATH: (
        "{\n"
        "  const src = new URL(document.currentScript.src);\n"
        '  let served = Number(src.searchParams.get("reloads"));\n'
        '  if (typeof SharedWorker === "function") {\n'
        f'    const url = new URL("{_WORKER_PATH}", src);\n'
        '    const port = new SharedWorker(url, "reloads").port;\n'
        "    port.onmessage = () => location.reload();\n"
        "    port.postMessage(served);\n"
        '    addEventListener("pagehide", () => port.postMessage(false));\n'
        '    addEventListener("pageshow", (event) => {\n'
        "      if (event.persisted) port.postMessage(served);\n"
        "    });\n"
        "  } else {\n"
        f'    const url = new URL("{_EVENTS_PATH}", src);\n'
        "    const events = new EventSource(url);\n"
        "    events.onmessage = (event) => {\n"
        "      const reloads = Number(event.data);\n"
        "      if (served < reloads) location.reload();\n"
        "      else served = reloads;\n"
        "    };\n"
        "  }\n"
        "}\n"
    ),
    # Pages say whether they're open, since one can cancel its reload or come back
    # from the back/forward cache.
    #
    # The stream opens once a page connects, so no reload arrives with no page to tell.
    # Its count is forgotten when it drops, since the server may have restarted.
    _WORKER_PATH: (
        "const pages = new Map();\n"
        "let events = null;\n"
        "let reloads = null;\n"
        "onconnect = (event) => {\n"
        "  const port = event.ports[0];\n"
        "  port.onmessage = ({ data }) => {\n"
        "    if (data === false) return pages.delete(port);\n"
        "    if (reloads !== null) data = Math.min(data, reloads);\n"
        "    pages.set(port, data);\n"
        "    if (data < reloads) port.postMessage(null);\n"
        "  };\n"
        "  if (events) return;\n"
        f'  events = new EventSource("{_EVENTS_PATH}");\n'
        "  events.onerror = () => {\n"
        "    reloads = null;\n"
        "  };\n"
        "  events.onmessage = (event) => {\n"
        "    reloads = Number(event.data);\n"
        "    for (const [port, served] of pages) {\n"
        "      if (served < reloads) port.postMessage(null);\n"
        "      else pages.set(port, reloads);\n"
        "    }\n"
        "  };\n"
        "};\n"
    ),
}


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
        self._served = 0

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
            outputfile.write(cast("AnyStr", self._script_tag()))

    def do_GET(self) -> None:
        """Serve a GET request."""
        if urlsplit(self.path).path == _EVENTS_PATH:
            self._send_events()
        else:
            super().do_GET()

    def end_headers(self) -> None:
        """End the response's headers, telling browsers not to cache it."""
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

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

        # Counted before the file is read, so a reload while it's read isn't missed.
        self._served = self._reloader.reloads

        return super().send_head()

    def send_header(self, keyword: str, value: str) -> None:
        """Send a header, counting the reload script in an HTML body's length."""
        keyword_lower = keyword.lower()

        # Errors write their HTML bodies directly, without the script. HTML that scripts
        # fetch goes into pages that already have it.
        if self._ok and keyword_lower == "content-type":
            self._html = (
                value.partition(";")[0].strip().lower() == "text/html"
                and self.headers.get("Sec-Fetch-Dest") != "empty"
            )

        if self._html and keyword_lower == "content-length":
            value = str(int(value) + len(self._script_tag()))

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
        path = Path(self.translate_path(self.path))

        # Links can lead into the directory or out of it.
        target = path.resolve()

        # A missing file can't be served anyway.
        with suppress(OSError):
            return any(
                p.samefile(reserved)
                for p in (path, *path.parents, target, *target.parents)
            )

        return False

    def _script_tag(self) -> bytes:
        """The tag that adds the reload script to an HTML body."""
        # Absolute, since a page's <base> can lead elsewhere.
        host = self.headers.get("Host")
        path = f"{_SCRIPT_PATH}?reloads={self._served}"
        src = f"http://{host}{path}" if host else path
        return f'<script src="{escape(src)}"></script>'.encode()

    def _send_events(self) -> None:
        """Send the reloads so far, then again each time pages reload, until stopped."""
        seen = self._reloader.reloads

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", "text/event-stream")
        self.end_headers()

        # The page has gone.
        with suppress(OSError):
            self.wfile.write(f"data: {seen}\n\n".encode())

            while (reloads := self._reloader.wait(seen, _HEARTBEAT)) is not None:
                self.wfile.write(
                    f"data: {reloads}\n\n".encode() if reloads != seen else b":\n\n",
                )

                seen = reloads
