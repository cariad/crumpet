import sys
from socket import has_dualstack_ipv6

from notwork import has_ipv6
from pytest import mark

needs_dual_stack = mark.skipif(
    not has_dualstack_ipv6(),
    reason="No dual-stack sockets",
)
"""Decorator for tests that need a socket that accepts IPv4 and IPv6 at once."""


needs_ipv6 = mark.skipif(
    not has_ipv6(),
    reason="No IPv6",
)
"""Decorator for tests that need IPv6."""


posix_only = mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only behaviour",
)
"""Decorator for tests that must run on POSIX only."""


windows_only = mark.skipif(
    sys.platform != "win32",
    reason="Windows-only behaviour",
)
"""Decorator for tests that must run on Windows only."""
