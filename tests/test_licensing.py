"""Testes da superfície local de licenciamento."""

from __future__ import annotations

import unittest

import licensing


class LicensingIdentityTests(unittest.TestCase):
    def test_machine_fingerprint_formats_remain_stable(self) -> None:
        self.assertTrue(licensing.get_machine_fingerprint_v1().startswith("NXJ-"))
        self.assertTrue(licensing.get_machine_fingerprint_v2().startswith("NXJ2-"))

    def test_legacy_activation_entrypoint_is_absent(self) -> None:
        self.assertFalse(hasattr(licensing, "activate_software"))


if __name__ == "__main__":
    unittest.main()
