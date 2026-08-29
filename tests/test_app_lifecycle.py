from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import configure_shutdown_handlers


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class AppLifecycleTests(unittest.TestCase):
    def test_closing_is_blocked_when_user_keeps_active_work(self) -> None:
        closing = FakeEvent()
        window = SimpleNamespace(
            events=SimpleNamespace(closing=closing, closed=FakeEvent()),
            create_confirmation_dialog=MagicMock(return_value=False),
        )
        api = SimpleNamespace(
            has_active_work=MagicMock(return_value=True),
            shutdown_for_close=MagicMock(return_value=True),
        )

        configure_shutdown_handlers(window, api)

        self.assertFalse(closing.handlers[0]())
        api.shutdown_for_close.assert_not_called()

    def test_closing_waits_for_coordinated_shutdown(self) -> None:
        closing = FakeEvent()
        closed = FakeEvent()
        window = SimpleNamespace(
            events=SimpleNamespace(closing=closing, closed=closed),
            create_confirmation_dialog=MagicMock(return_value=True),
        )
        api = SimpleNamespace(
            has_active_work=MagicMock(return_value=True),
            shutdown_for_close=MagicMock(return_value=True),
        )

        configure_shutdown_handlers(window, api)

        self.assertTrue(closing.handlers[0]())
        api.shutdown_for_close.assert_called_once_with(timeout_seconds=15.0)
        closed.handlers[0]()
        api.shutdown_for_close.assert_called_with(timeout_seconds=2.0)


if __name__ == "__main__":
    unittest.main()
