"""Testes unitários para o módulo de licenciamento por hardware (licensing.py)."""

from __future__ import annotations

import re
import tempfile
import unittest
from unittest.mock import MagicMock

from licensing import (
    activate_software,
    deactivate_software,
    generate_activation_key,
    get_machine_fingerprint,
    is_software_activated,
    verify_license_key,
)
from web_api import BridgeApi


class LicensingUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.machine_id = get_machine_fingerprint()

    def tearDown(self) -> None:
        try:
            self.tmp_dir.cleanup()
        except OSError:
            pass

    def test_machine_fingerprint_format(self) -> None:
        """Verifica se o Machine ID segue o formato padrão NXJ-XXXX-XXXX-XXXX-XXXX."""
        self.assertTrue(self.machine_id.startswith("NXJ-"))
        pattern = r"^NXJ-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$"
        self.assertTrue(bool(re.match(pattern, self.machine_id)), f"Formato inválido: {self.machine_id}")

    def test_activation_key_generation_and_verification(self) -> None:
        """Testa a geração e verificação da assinatura criptográfica HMAC-SHA256."""
        key = generate_activation_key(self.machine_id)
        self.assertTrue(key.startswith("ACT-"))
        pattern = r"^ACT-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$"
        self.assertTrue(bool(re.match(pattern, key)), f"Formato da chave inválido: {key}")

        # Chave correta deve validar
        self.assertTrue(verify_license_key(self.machine_id, key))

        # Chave falsa ou alterada deve falhar
        self.assertFalse(verify_license_key(self.machine_id, "ACT-0000-1111-2222-3333"))
        self.assertFalse(verify_license_key("NXJ-9999-8888-7777-6666", key))

    def test_activation_lifecycle(self) -> None:
        """Testa o ciclo de ativação, persistência e desativação."""
        # 1. Desativação
        deactivate_software()
        is_act, mid = is_software_activated()
        self.assertFalse(is_act)
        self.assertEqual(mid, self.machine_id)

        # 2. Tentativa com chave inválida
        bad_res = activate_software("ACT-INVALID-KEY-1234")
        self.assertFalse(bad_res["ok"])
        is_act2, _ = is_software_activated()
        self.assertFalse(is_act2)

        # 3. Ativação com chave correta
        good_key = generate_activation_key(self.machine_id)
        good_res = activate_software(good_key)
        self.assertTrue(good_res["ok"])

        # 4. Verificação de estado ativo
        is_act3, mid3 = is_software_activated()
        self.assertTrue(is_act3)
        self.assertEqual(mid3, self.machine_id)


class WebApiLicensingBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = BridgeApi()
        self.api._window = MagicMock()
        self.machine_id = get_machine_fingerprint()

    def test_bridge_license_endpoints(self) -> None:
        """Testa as chamadas de licença expostas através da BridgeApi."""
        info = self.api.get_license_info()
        self.assertIn("is_activated", info)
        self.assertIn("machine_id", info)
        self.assertEqual(info["machine_id"], self.machine_id)

        valid_key = generate_activation_key(self.machine_id)
        act_res = self.api.activate_software(valid_key)
        self.assertTrue(act_res["ok"])


if __name__ == "__main__":
    unittest.main()
