from collections.abc import Generator
from contextlib import closing, contextmanager
from http.client import HTTPConnection, HTTPResponse
from threading import Event
from typing import NamedTuple
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from crumpet import Server


class Response(NamedTuple):
    """A response, without its date."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes


def fetch(url: str) -> Response:
    """Get `url`, without following a redirect."""
    with get(url) as response:
        return Response(
            status=response.status,
            headers=[
                (name, value) for name, value in response.getheaders() if name != "Date"
            ],
            body=response.read(),
        )


@contextmanager
def get(url: str) -> Generator[HTTPResponse]:
    """Get `url`, without following a redirect, and yield its response."""
    parts = urlsplit(url)

    with closing(
        HTTPConnection(
            parts.netloc,
            timeout=5,
        ),
    ) as connection:
        connection.request(
            "GET",
            parts.path,
        )

        yield connection.getresponse()


def next_event(page: HTTPResponse) -> bytes:
    """
    Read the next event or heartbeat that `page` is sent, and return its top line
    without the count of reloads, which changes made before watching can raise.
    """
    line = page.readline()

    while page.readline() not in (b"\n", b""):
        pass

    return line.translate(
        None,
        b"0123456789",
    )


@contextmanager
def open_page(url: str) -> Generator[HTTPResponse]:
    """
    Connect to the server at `url` as an open page does, and yield its events after
    the first, which counts the reloads so far.
    """
    with get(urljoin(url, "/__crumpet__/events")) as page:
        next_event(page)
        yield page


def reloads(page: Page, server: Server, events_opened: Event) -> bool:
    """
    Reload `server`'s pages once `page` opens its event stream, and return whether
    `page` reloaded.
    """
    if not events_opened.wait(5):
        return False

    try:
        with page.expect_event("load", timeout=5000):
            server.reload()

    except PlaywrightTimeoutError:
        return False

    return True


def settle(page: HTTPResponse) -> None:
    """
    Wait for a heartbeat on `page`, so that changes made before watching started can't
    be mistaken for later ones. macOS can report them once watching has started.
    """
    while (event := next_event(page)) != b":\n":
        if not event:
            raise EOFError
