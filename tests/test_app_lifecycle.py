from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app as app_module
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


class SingleInstanceTests(unittest.TestCase):
    def test_non_windows_does_not_create_mutex(self) -> None:
        with patch.object(app_module.sys, "platform", "linux"):
            self.assertEqual(app_module._acquire_gui_instance_lock(), (None, False))

    def test_windows_mutex_reports_existing_instance(self) -> None:
        create_mutex = MagicMock(return_value=123)
        kernel32 = SimpleNamespace(CreateMutexW=create_mutex)
        with (
            patch.object(app_module.sys, "platform", "win32"),
            patch.object(app_module.ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(app_module.ctypes, "get_last_error", return_value=183, create=True),
        ):
            handle, already_running = app_module._acquire_gui_instance_lock()

        self.assertEqual(handle, 123)
        self.assertTrue(already_running)
        create_mutex.assert_called_once_with(None, False, app_module._SINGLE_INSTANCE_MUTEX_NAME)

    def test_windows_mutex_failure_raises_native_error(self) -> None:
        kernel32 = SimpleNamespace(CreateMutexW=MagicMock(return_value=None))
        native_error = OSError("mutex indisponível")
        with (
            patch.object(app_module.sys, "platform", "win32"),
            patch.object(app_module.ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(app_module.ctypes, "get_last_error", return_value=5, create=True),
            patch.object(app_module.ctypes, "WinError", return_value=native_error, create=True),
            self.assertRaisesRegex(OSError, "mutex indisponível"),
        ):
            app_module._acquire_gui_instance_lock()

    def test_main_warns_and_releases_existing_instance_handle(self) -> None:
        with (
            patch.object(app_module.sys, "argv", ["app.py"]),
            patch.object(app_module, "configure_production_diagnostics"),
            patch.object(app_module, "_acquire_gui_instance_lock", return_value=(123, True)),
            patch.object(app_module, "_release_gui_instance_lock") as release_lock,
            patch.object(app_module, "_show_message") as show_message,
            patch.object(app_module, "run_gui") as run_gui,
        ):
            result = app_module.main()

        self.assertEqual(result, 0)
        release_lock.assert_called_once_with(123)
        run_gui.assert_not_called()
        self.assertIn("já está aberto", show_message.call_args.args[0])
        self.assertFalse(show_message.call_args.kwargs["error"])

    def test_main_releases_mutex_when_gui_fails(self) -> None:
        with (
            patch.object(app_module.sys, "argv", ["app.py"]),
            patch.object(app_module, "configure_production_diagnostics"),
            patch.object(app_module, "_acquire_gui_instance_lock", return_value=(456, False)),
            patch.object(app_module, "_release_gui_instance_lock") as release_lock,
            patch.object(app_module, "validate_runtime_dependencies"),
            patch.object(app_module, "run_gui", side_effect=RuntimeError("falha simulada")),
            patch.object(app_module, "log_startup_failure"),
            patch.object(app_module, "diagnostic_status", return_value={"configured": False}),
            patch.object(app_module, "_show_message"),
        ):
            result = app_module.main()

        self.assertEqual(result, 1)
        release_lock.assert_called_once_with(456)

    def test_quick_convert_bypasses_gui_mutex(self) -> None:
        with (
            patch.object(app_module.sys, "argv", ["app.py", "documento.pdf"]),
            patch.object(app_module, "configure_production_diagnostics"),
            patch.object(app_module, "run_quick_convert") as quick_convert,
            patch.object(app_module, "_acquire_gui_instance_lock") as acquire_lock,
        ):
            result = app_module.main()

        self.assertEqual(result, 0)
        quick_convert.assert_called_once_with(["documento.pdf"])
        acquire_lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
