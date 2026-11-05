from errno import EADDRNOTAVAIL, EAFNOSUPPORT
from socket import AF_INET6, SOCK_STREAM, socket

# The errors that mean this machine has no IPv6.
_NO_IPV6 = (
    EADDRNOTAVAIL,  # IPv6 is turned off, so ::1 doesn't exist
    EAFNOSUPPORT,  # The system has no IPv6 at all
)


def has_ipv6() -> bool:
    """
    Check whether or not this machine has IPv6.

    Returns:
        `True` when this machine has IPv6, otherwise `False`.

    Raises:
        OSError: When the check can't be performed.
    """
    try:
        with socket(AF_INET6, SOCK_STREAM) as sock:
            sock.bind(("::1", 0))

    except OSError as ex:
        if ex.errno in _NO_IPV6:
            return False

        raise

    return True
