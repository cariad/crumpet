from collections.abc import Generator
from contextlib import AbstractContextManager, closing, contextmanager
from http.client import HTTPConnection, HTTPResponse
from urllib.parse import urljoin, urlsplit


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
    """Read the next event or heartbeat that `page` is sent, without its blank line."""
    line = page.readline()
    page.readline()
    return line


def open_page(url: str) -> AbstractContextManager[HTTPResponse]:
    """Connect to the server at `url` as an open page does, and yield its events."""
    return get(urljoin(url, "/__crumpet__/events"))


def settle(page: HTTPResponse) -> None:
    """
    Wait for a heartbeat on `page`, so that changes made before watching started can't
    be mistaken for later ones. macOS can report them once watching has started.
    """
    while (event := next_event(page)) != b":\n":
        if not event:
            raise EOFError
