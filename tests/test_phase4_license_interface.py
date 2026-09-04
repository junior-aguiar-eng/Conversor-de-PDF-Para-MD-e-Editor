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

    def test_verificacao_manual_e_totalmente_local(self) -> None:
        status = LicenseStatus(
            state=LicenseState.EXPIRING,
            machine_id="NXJ2-TESTE",
            message="Licença válida e próxima do vencimento.",
            license_id="LIC-2026-000001",
            revision=2,
            expires_at="2027-01-15T12:00:00Z",
            days_remaining=80,
            features=("converter",),
        )
        with patch("web_api.get_license_status", return_value=status):
            result = self.api.verify_license_now()

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "expiring")
        self.assertEqual(result["days_remaining"], 80)

    def test_encerramento_pelo_bloqueio_nao_libera_a_interface(self) -> None:
        with (
            patch.object(self.api, "has_active_work", return_value=False),
            patch.object(self.api, "shutdown_for_close", return_value=True),
        ):
            result = self.api.exit_application()

        self.assertTrue(result["ok"])
        self.api._window.destroy.assert_called_once_with()


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
        self.assertIn("Encerrar aplicativo", self.html)
        self.assertIn("appLicense.copyMachineId()", self.html)
        self.assertLess(self.html.index('src="license-ui.js"'), self.html.index('src="app.js"'))

    def test_modal_exibe_identidade_e_revisao_sem_conceitos_online(self) -> None:
        self.assertIn("Identificação da licença", self.html)
        self.assertIn("Revisão vigente", self.html)
        self.assertNotIn("Uso offline restante", self.html)
        self.assertNotIn("Última validação online", self.html)
        self.assertIn("mesma licença", self.html)

    def test_importacao_pertence_ao_dialogo_de_licenca(self) -> None:
        license_start = self.html.index('id="activationModal"')
        license_end = self.html.index('id="welcomeModal"')
        import_button = self.html.index('id="btnImportLicense"')
        self.assertLess(license_start, import_button)
        self.assertLess(import_button, license_end)

    def test_bloqueio_oferece_encerramento_sem_liberar_interface(self) -> None:
        self.assertIn('id="btnCloseLicense"', self.html)
        self.assertIn('id="btnExitLicense"', self.html)
        self.assertIn('data-action="appLicense.exitApplication()"', self.html)


if __name__ == "__main__":
    unittest.main()
