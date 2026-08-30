"""Resiliência para integrações online opcionais da aplicação."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ServiceCircuitOpen(RuntimeError):
    """Indica que novas chamadas foram suspensas após falhas consecutivas."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        super().__init__("Serviço temporariamente suspenso após falhas consecutivas.")


class ServiceOperationTimeout(TimeoutError):
    """Indica que o orçamento global da operação online foi excedido."""


@dataclass(frozen=True)
class ResilientCallResult:
    value: Any
    attempts: int


class CircuitBreaker:
    """Circuit breaker thread-safe com uma única sondagem após o intervalo."""

    def __init__(
        self,
        failure_threshold: int,
        reset_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1 or reset_seconds < 0:
            raise ValueError("Configuração inválida do circuit breaker.")
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state = "closed"
        self._failures = 0
        self._opened_at: float | None = None
        self._last_error: str | None = None
        self._probe_in_flight = False

    def before_call(self) -> None:
        with self._lock:
            if self._state == "closed":
                return
            now = self._clock()
            opened_at = self._opened_at if self._opened_at is not None else now
            elapsed = now - opened_at
            if self._state == "open" and elapsed >= self._reset_seconds:
                self._state = "half_open"
                self._probe_in_flight = True
                return
            retry_after = self._reset_seconds - elapsed if self._state == "open" else self._reset_seconds
            raise ServiceCircuitOpen(retry_after)

    def record_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._opened_at = None
            self._last_error = None
            self._probe_in_flight = False

    def record_failure(self, error: BaseException) -> None:
        with self._lock:
            self._failures += 1
            self._last_error = type(error).__name__
            if self._state == "half_open" or self._failures >= self._failure_threshold:
                self._state = "open"
                self._opened_at = self._clock()
            self._probe_in_flight = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            retry_after = 0.0
            if self._state == "open" and self._opened_at is not None:
                retry_after = max(0.0, self._reset_seconds - (self._clock() - self._opened_at))
            return {
                "state": self._state,
                "consecutive_failures": self._failures,
                "retry_after_seconds": round(retry_after, 1),
                "last_error_type": self._last_error,
            }


def _run_with_timeout(operation: Callable[[], Any], timeout_seconds: float) -> Any:
    """Executa trabalho bloqueante sem permitir que ele retenha a thread da ponte."""
    outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            outcome.put((True, operation()))
        except BaseException as error:
            outcome.put((False, error))

    worker = threading.Thread(target=target, name="OptionalOnlineService", daemon=True)
    worker.start()
    worker.join(max(0.0, timeout_seconds))
    if worker.is_alive():
        raise ServiceOperationTimeout("A operação online excedeu o tempo máximo.")
    succeeded, value = outcome.get_nowait()
    if succeeded:
        return value
    raise value  # type: ignore[misc]


def call_with_resilience(
    operation: Callable[[], Any],
    breaker: CircuitBreaker,
    *,
    timeout_seconds: float,
    max_attempts: int,
    backoff_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ResilientCallResult:
    """Aplica prazo global, repetição exponencial limitada e circuit breaker."""
    if timeout_seconds <= 0 or max_attempts < 1 or backoff_seconds < 0:
        raise ValueError("Política de resiliência inválida.")
    breaker.before_call()
    deadline = clock() + timeout_seconds
    last_error: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        remaining = deadline - clock()
        if remaining <= 0:
            last_error = ServiceOperationTimeout("A operação online excedeu o tempo máximo.")
            break
        try:
            value = _run_with_timeout(operation, remaining)
        except BaseException as error:
            last_error = error
        else:
            breaker.record_success()
            return ResilientCallResult(value=value, attempts=attempt)

        if attempt < max_attempts:
            remaining = deadline - clock()
            delay = min(backoff_seconds * (2 ** (attempt - 1)), max(0.0, remaining))
            if delay > 0:
                sleeper(delay)

    assert last_error is not None
    breaker.record_failure(last_error)
    raise last_error
