from socket import has_dualstack_ipv6

from pytest import mark

from crumpet import has_ipv6

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
