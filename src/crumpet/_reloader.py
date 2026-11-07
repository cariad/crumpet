from threading import Condition
from uuid import uuid4


class Reloader:
    """Tells open pages when to reload."""

    def __init__(self) -> None:
        self._closed = False
        self._condition = Condition()
        self._reloads = 0

        self.run = uuid4().hex
        """ID that pages from an earlier server won't match."""

    def close(self) -> None:
        """Stop every page from waiting."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def reload(self) -> None:
        """Tell every waiting page to reload."""
        with self._condition:
            self._reloads += 1
            self._condition.notify_all()

    @property
    def reloads(self) -> int:
        """Number of reloads so far."""
        with self._condition:
            return self._reloads

    def wait(self, seen: int, timeout: float) -> int | None:
        """
        Wait for a reload that the caller hasn't seen yet, or until `timeout` passes.

        Args:
            seen: Number of reloads the caller already knows about, from
                `reloads` or the previous `wait`.
            timeout: Seconds to wait.

        Returns:
            Number of reloads so far, or `None` once closed.
        """
        with self._condition:
            self._condition.wait_for(
                lambda: self._closed or self._reloads != seen,
                timeout,
            )
            return None if self._closed else self._reloads
