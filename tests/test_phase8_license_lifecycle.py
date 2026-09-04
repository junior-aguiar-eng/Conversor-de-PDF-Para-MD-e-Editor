from __future__ import annotations

import unittest
from pathlib import Path


class OfflineLifecycleSurfaceTests(unittest.TestCase):
    def test_admin_surface_has_only_essential_license_actions(self) -> None:
        root = Path(__file__).parents[1]
        html = (root / "admin_web" / "index.html").read_text(encoding="utf-8")
        script = (root / "admin_web" / "app.js").read_text(encoding="utf-8")
        for label in ("Emitir licença", "Renovar", "Trocar computador", "Reexportar arquivo"):
            self.assertIn(label, html)
        for obsolete in ("Migrar ACT2/ACT3", "Suspender", "Revogar", "Aguardando conexão"):
            self.assertNotIn(obsolete, html + script)


if __name__ == "__main__":
    unittest.main()
