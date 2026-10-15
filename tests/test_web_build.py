"""Compila o CSS real e verifica os estilos exigidos pela interface existente."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class WebBuildTests(unittest.TestCase):
    def test_installed_tailwind_compiles_existing_theme_and_responsive_utilities(self):
        root = Path(__file__).resolve().parents[1]
        metadata = root / 'node_modules/@tailwindcss/cli/package.json'
        node = shutil.which('node')
        if not node or not metadata.is_file():
            self.skipTest('Execute npm ci para provisionar o compilador CSS.')
        package = json.loads(metadata.read_text(encoding='utf-8'))
        self.assertIn('tailwindcss', package.get('bin', {}), 'O build exige o CLI compatível com a configuração existente.')
        cli = metadata.parent / package['bin']['tailwindcss']
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'tailwind.css'
            result = subprocess.run(
                [node, str(cli), '-i', 'web/tailwind.input.css',
                 '-o', str(output), '--minify'],
                cwd=root, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            css = output.read_text(encoding='utf-8')
            for expected in ('.selection\\:bg-brand-200', '.shadow-glass{', '.font-sans{',
                             '.sm\\:grid-cols-2{', 'box-sizing:border-box', '.bg-gradient-to-br{',
                             '.focus\\:outline-hidden:focus', 'outline:2px solid #0000'):
                self.assertTrue(expected in css, f'Estilo necessário ausente: {expected}')
            self.assertNotIn('@tailwind ', css)
            self.assertTrue(re.search(r'\.shadow-sm[^{}]*\{[^}]*--tw-shadow:0 1px 2px 0', css))


if __name__ == '__main__':
    unittest.main()
