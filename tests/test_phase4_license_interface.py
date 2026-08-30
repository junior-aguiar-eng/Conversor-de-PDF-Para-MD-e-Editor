from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from license_core import LicenseState, LicenseStatus
from web_api import BridgeApi


class Phase4LicenseBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch("web_api.LibraryDatabase", return_value=MagicMock()):
            self.api = BridgeApi(conversion_journal_path=Path(self.temp.name) / "journal.json")
        self.api._window = MagicMock()

    def test_importa_nxjlic_pelo_dialogo_sem_expor_caminho_ao_renderer(self) -> None:
        document = Path(self.temp.name) / "licenca.nxjlic"
        document.write_bytes(b'{"schema":"nexojuris-license/v1"}')
        self.api._window.create_file_dialog.return_value = (str(document),)

        with patch("web_api.activate_act4_license", return_value={"ok": True, "message": "importada"}) as activate:
            result = self.api.import_license_file()

        self.assertTrue(result["ok"])
        activate.assert_called_once_with(document.read_bytes())
        self.assertNotIn("path", result)

    def test_rejeita_arquivo_de_licenca_com_extensao_incorreta(self) -> None:
        document = Path(self.temp.name) / "licenca.json"
        document.write_text("{}", encoding="utf-8")
        self.api._window.create_file_dialog.return_value = (str(document),)

        result = self.api.import_license_file()

        self.assertFalse(result["ok"])
        self.assertIn(".nxjlic", result["error"])

    def test_verificacao_manual_preserva_distincao_do_prazo_offline(self) -> None:
        status = LicenseStatus(
            state=LicenseState.ONLINE_CHECK_REQUIRED,
            machine_id="NXJ2-TESTE",
            message="É necessária uma validação online para continuar.",
            license_format="act4",
            validation_mode="hybrid",
            expires_at="2027-01-15T12:00:00Z",
            days_remaining=80,
            offline_until="2026-08-30T12:00:00Z",
            offline_seconds_remaining=0,
        )
        with patch("web_api.get_license_status", return_value=status):
            result = self.api.verify_license_now()

        self.assertFalse(result["ok"])
        self.assertFalse(result["online_attempted"])
        self.assertEqual(result["state"], "online_check_required")
        self.assertIn("prazo de uso offline", result["message"])
        self.assertEqual(result["days_remaining"], 80)


class Phase4LicenseMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    def test_dialogo_e_alertas_possuem_semantica_acessivel(self) -> None:
        self.assertIn('id="activationModal"', self.html)
        self.assertIn('role="dialog" aria-modal="true"', self.html)
        self.assertIn('aria-labelledby="licenseDialogTitle"', self.html)
        self.assertIn('id="licenseStatePanel"', self.html)
        self.assertIn('aria-live="polite" aria-atomic="true"', self.html)
        self.assertIn('id="activationAlertBox" role="status" aria-live="polite"', self.html)

    def test_acoes_obrigatorias_estao_disponiveis_sem_recurso_externo(self) -> None:
        self.assertIn("Verificar licença agora", self.html)
        self.assertIn("Importar arquivo de licença (.nxjlic)", self.html)
        self.assertIn("appLicense.copyMachineId()", self.html)
        self.assertLess(self.html.index('src="license-ui.js"'), self.html.index('src="app.js"'))


if __name__ == "__main__":
    unittest.main()
