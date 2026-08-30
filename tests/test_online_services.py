from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import Mock, patch

import web_api
from online_services import (
    CircuitBreaker,
    ServiceCircuitOpen,
    ServiceOperationTimeout,
    call_with_resilience,
)
from web_api import BridgeApi


class OnlineServicesResilienceTests(unittest.TestCase):
    def test_retry_recovers_and_resets_breaker(self) -> None:
        breaker = CircuitBreaker(3, 60)
        calls = 0

        def operation() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("offline")
            return "ok"

        result = call_with_resilience(
            operation,
            breaker,
            timeout_seconds=1,
            max_attempts=3,
            backoff_seconds=0,
        )

        self.assertEqual(result.value, "ok")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(breaker.status()["state"], "closed")
        self.assertEqual(breaker.status()["consecutive_failures"], 0)

    def test_global_timeout_returns_without_waiting_for_blocked_provider(self) -> None:
        release = threading.Event()
        breaker = CircuitBreaker(1, 60)
        started = time.monotonic()

        with self.assertRaises(ServiceOperationTimeout):
            call_with_resilience(
                lambda: release.wait(1),
                breaker,
                timeout_seconds=0.02,
                max_attempts=1,
                backoff_seconds=0,
            )

        self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(breaker.status()["state"], "open")
        release.set()

    def test_circuit_opens_and_allows_one_probe_after_reset(self) -> None:
        now = [0.0]
        breaker = CircuitBreaker(2, 10, clock=lambda: now[0])
        breaker.record_failure(ConnectionError())
        breaker.record_failure(ConnectionError())

        with self.assertRaises(ServiceCircuitOpen):
            breaker.before_call()

        now[0] = 10.0
        breaker.before_call()
        self.assertEqual(breaker.status()["state"], "half_open")
        with self.assertRaises(ServiceCircuitOpen):
            breaker.before_call()
        breaker.record_success()
        self.assertEqual(breaker.status()["state"], "closed")

    @patch.object(web_api, "ONLINE_SERVICE_BACKOFF_SECONDS", 0)
    def test_translation_retries_and_reports_operational_state(self) -> None:
        api = BridgeApi.__new__(BridgeApi)
        translator = Mock()
        translator.translate.side_effect = [ConnectionError("offline"), "Olá"]

        with patch("web_api.GoogleTranslator", return_value=translator):
            result = api.translate_text("Hello", target_lang="pt", source_lang="auto")

        self.assertTrue(result["ok"])
        self.assertEqual(result["translated_text"], "Olá")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["service_status"]["state"], "closed")
        self.assertFalse(result["service_status"]["critical_use_recommended"])

    @patch.object(web_api, "ONLINE_SERVICE_BACKOFF_SECONDS", 0)
    def test_tts_retries_after_transient_failure(self) -> None:
        api = BridgeApi.__new__(BridgeApi)
        calls = 0

        class FailingCommunication:
            async def stream(self):
                raise ConnectionError("offline")
                yield  # pragma: no cover

        class SuccessfulCommunication:
            async def stream(self):
                yield {"type": "audio", "data": b"audio"}

        def communicate(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return FailingCommunication() if calls == 1 else SuccessfulCommunication()

        with patch("web_api.edge_tts.Communicate", side_effect=communicate):
            result = api.synthesize_speech("Texto")

        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(calls, 2)

    @patch.object(web_api, "ONLINE_SERVICE_MAX_ATTEMPTS", 1)
    @patch.object(web_api, "ONLINE_SERVICE_BACKOFF_SECONDS", 0)
    def test_translation_circuit_does_not_disable_tts(self) -> None:
        api = BridgeApi.__new__(BridgeApi)

        with patch("web_api.GoogleTranslator", side_effect=ConnectionError("offline")):
            for _ in range(web_api.ONLINE_SERVICE_CIRCUIT_FAILURE_THRESHOLD):
                result = api.translate_text("Hello")

        blocked = api.translate_text("Hello")
        status = api.get_online_services_status()

        self.assertFalse(result["ok"])
        self.assertEqual(blocked["error_code"], "circuit_open")
        self.assertEqual(status["services"]["translation"]["state"], "open")
        self.assertEqual(status["services"]["tts"]["state"], "closed")


if __name__ == "__main__":
    unittest.main()
