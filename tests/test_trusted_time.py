"""Gate da Fase 3: persistência temporal, retrocesso e recuperação."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from trusted_time import DpapiProtector, TemporalGuard, TemporalStateError


class ReversingProtector:
    PREFIX = b"PROTECTED:"

    def protect(self, plaintext: bytes) -> bytes:
        return self.PREFIX + plaintext[::-1]

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(self.PREFIX):
            raise TemporalStateError("ciphertext inválido")
        return ciphertext.removeprefix(self.PREFIX)[::-1]


class MemoryAnchor:
    def __init__(self) -> None:
        self.value: bytes | None = None

    def read(self) -> bytes | None:
        return self.value

    def write(self, value: bytes) -> None:
        self.value = value


class TrustedTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "license-time.dat"
        self.anchor = MemoryAnchor()
        self.guard = TemporalGuard(self.path, protector=ReversingProtector(), anchor=self.anchor)
        self.initial = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    def test_first_observation_is_protected_and_persisted_redundantly(self) -> None:
        result = self.guard.observe(self.initial)
        self.assertFalse(result.clock_tampered)
        self.assertEqual(result.effective_time, self.initial)
        self.assertNotIn(b"2026-10-01", self.path.read_bytes())
        self.assertEqual(self.path.read_bytes(), self.anchor.value)

    def test_clock_advanced_then_retroceded_is_detected(self) -> None:
        self.guard.observe(self.initial)
        advanced = self.initial + timedelta(days=10)
        self.guard.observe(advanced)
        result = self.guard.observe(self.initial)
        self.assertTrue(result.clock_tampered)
        self.assertEqual(result.effective_time, advanced)

    def test_small_clock_adjustment_is_tolerated_without_extending_time(self) -> None:
        self.guard.observe(self.initial)
        result = self.guard.observe(self.initial - timedelta(minutes=4))
        self.assertFalse(result.clock_tampered)
        self.assertEqual(result.effective_time, self.initial)

    def test_timezone_change_preserves_the_same_instant(self) -> None:
        self.guard.observe(self.initial)
        local_equivalent = self.initial.astimezone(timezone(timedelta(hours=-3)))
        result = self.guard.observe(local_equivalent)
        self.assertFalse(result.clock_tampered)
        self.assertEqual(result.effective_time, self.initial)

    def test_valid_server_time_recovers_from_clock_rollback(self) -> None:
        advanced = self.initial + timedelta(days=10)
        self.guard.observe(advanced)
        blocked = self.guard.observe(self.initial)
        self.assertTrue(blocked.clock_tampered)
        recovered = self.guard.observe(self.initial, trusted_server_time=advanced + timedelta(minutes=1))
        self.assertFalse(recovered.clock_tampered)
        self.assertEqual(recovered.last_trusted_server_at, advanced + timedelta(minutes=1))

    def test_reinstallation_without_primary_file_keeps_registry_anchor(self) -> None:
        advanced = self.initial + timedelta(days=10)
        self.guard.observe(advanced)
        self.path.unlink()
        reinstalled = TemporalGuard(self.path, protector=ReversingProtector(), anchor=self.anchor)
        result = reinstalled.observe(self.initial)
        self.assertTrue(result.clock_tampered)

    def test_restored_old_backup_cannot_lower_redundant_anchor(self) -> None:
        self.guard.observe(self.initial)
        old_backup = self.path.read_bytes()
        advanced = self.initial + timedelta(days=10)
        self.guard.observe(advanced)
        self.path.write_bytes(old_backup)
        result = self.guard.observe(self.initial)
        self.assertTrue(result.clock_tampered)
        self.assertEqual(result.max_observed_at, advanced)

    def test_one_valid_anchor_recovers_a_corrupted_copy(self) -> None:
        self.guard.observe(self.initial)
        self.path.write_bytes(b"corrompido")
        result = self.guard.observe(self.initial)
        self.assertFalse(result.clock_tampered)

    def test_both_corrupted_anchors_fail_closed(self) -> None:
        self.path.write_bytes(b"corrompido")
        self.anchor.value = b"tambem-corrompido"
        with self.assertRaises(TemporalStateError):
            self.guard.observe(self.initial)

    @unittest.skipUnless(__import__("sys").platform == "win32", "DPAPI exige Windows")
    def test_windows_dpapi_roundtrip(self) -> None:
        protector = DpapiProtector()
        plaintext = b'nexojuris-temporal-state-test'
        ciphertext = protector.protect(plaintext)
        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(protector.unprotect(ciphertext), plaintext)


if __name__ == "__main__":
    unittest.main()
