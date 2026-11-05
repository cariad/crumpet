import sys
from contextlib import ExitStack
from socket import (
    AF_INET,
    AF_INET6,
    IPPROTO_IPV6,
    IPV6_V6ONLY,
    SO_REUSEADDR,
    SOCK_STREAM,
    SOL_SOCKET,
    AddressFamily,
    socket,
)

from .has_ipv6 import has_ipv6

# The addresses that `localhost` can mean.
_LOOPBACKS = {
    AF_INET: "127.0.0.1",
    AF_INET6: "::1",
}

# Lets a socket bind while connections from a server are still closing, because they
# don't make the port busy.
_REUSE_ADDRESS = sys.platform != "win32"

# The addresses that a program listens on to accept connections on every address,
# loopback included, and whether the socket accepts IPv4 and IPv6 at once.
#
# macOS and Windows let a socket bind a loopback address while another program listens
# on its wildcard, so they bind the wildcard first, to find that program. Both always
# have IPv6, so both wildcards can be bound.
#
# Linux refuses that bind, and binding the wildcard there would also find programs that
# listen only on other addresses, which `localhost` doesn't reach.
#
# Windows lets a socket that accepts IPv4 and IPv6 at once share its port with sockets
# that accept only one, so the IPv6 wildcard is bound both ways.
_WILDCARDS: tuple[tuple[AddressFamily, str, bool], ...] = (
    ()
    if sys.platform == "linux"
    else (
        (AF_INET, "0.0.0.0", False),
        (AF_INET6, "::", False),
        (AF_INET6, "::", True),
    )
)


def _bind(
    family: AddressFamily,
    host: str,
    port: int,
    *,
    dual_stack: bool = False,
) -> socket:
    """Bind a TCP socket to `host` and `port`."""
    sock = socket(family, SOCK_STREAM)

    try:
        sock.setsockopt(
            SOL_SOCKET,
            SO_REUSEADDR,
            _REUSE_ADDRESS,
        )

        if family == AF_INET6:
            sock.setsockopt(
                IPPROTO_IPV6,
                IPV6_V6ONLY,
                not dual_stack,
            )

        sock.bind((host, port))

    except BaseException:
        sock.close()
        raise

    return sock


def claim_port(port: int) -> list[socket]:
    """
    Claim `port` on every address that `localhost` can mean.

    Args:
        port: Port to claim.

    Returns:
        A socket bound to every address.

    Raises:
        OSError:
            - With `errno.EADDRINUSE` when another program accepts connections on `port`
              at an address that `localhost` can mean
            - The system's own error when `port` can't be claimed for any other reason.
    """
    families = (AF_INET, AF_INET6) if has_ipv6() else (AF_INET,)

    for family, wildcard, dual_stack in _WILDCARDS:  # pragma: macos-and-windows-only
        _bind(
            family,
            wildcard,
            port,
            dual_stack=dual_stack,
        ).close()

    with ExitStack() as stack:
        sockets = [
            stack.enter_context(
                _bind(
                    family,
                    _LOOPBACKS[family],
                    port,
                ),
            )
            for family in families
        ]

        # The sockets are the caller's to close now.
        stack.pop_all()

    return sockets
