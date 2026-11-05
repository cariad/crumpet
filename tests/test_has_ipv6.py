from errno import EADDRNOTAVAIL, EAFNOSUPPORT, EMFILE
from os import strerror
from socket import socket
from unittest.mock import patch

from pytest import raises

from crumpet import has_ipv6


class _SocketWithoutLoopback(socket):
    """A socket on a machine whose IPv6 is turned off, so that ::1 doesn't exist."""

    def bind(self, address: object, /) -> None:
        """Refuse to bind, as there's no ::1 to bind to."""
        raise OSError(
            EADDRNOTAVAIL,
            strerror(EADDRNOTAVAIL),
        )


def test_has_ipv6_is_false_without_ipv6_loopback() -> None:
    """Assert that a machine whose IPv6 is turned off has no IPv6."""
    with patch(
        "crumpet.has_ipv6.socket",
        _SocketWithoutLoopback,
    ):
        result = has_ipv6()

    expect = False

    assert result == expect


def test_has_ipv6_is_false_without_ipv6_support() -> None:
    """Assert that a system that can't make IPv6 sockets has no IPv6."""
    error = OSError(
        EAFNOSUPPORT,
        strerror(EAFNOSUPPORT),
    )

    with patch(
        "crumpet.has_ipv6.socket",
        side_effect=error,
    ):
        result = has_ipv6()

    expect = False

    assert result == expect


def test_has_ipv6_raises_when_it_cannot_check() -> None:
    """
    Assert that `has_ipv6` raises the system's error when it can't make the check,
    rather than guess.
    """
    error = OSError(
        EMFILE,
        strerror(EMFILE),
    )

    with (
        patch(
            "crumpet.has_ipv6.socket",
            side_effect=error,
        ),
        raises(OSError) as ex,
    ):
        has_ipv6()

    result = ex.value
    expect = error

    assert result is expect
