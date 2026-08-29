"""Falha o empacotamento se a interface segura/autocontida estiver incompleta."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
REQUIRED = (
    "tailwind.css",
    "style.css",
    "app.js",
    "vendor/marked.umd.js",
    "vendor/purify.min.js",
    "fonts/plus-jakarta-sans-latin.woff2",
    "fonts/inter-latin.woff2",
    "fonts/jetbrains-mono-latin.woff2",
    "fonts/cinzel-latin.woff2",
)


def verify() -> None:
    missing = [name for name in REQUIRED if not (WEB / name).is_file() or (WEB / name).stat().st_size == 0]
    if missing:
        raise RuntimeError(f"Assets web ausentes ou vazios: {', '.join(missing)}")

    index = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    style = (WEB / "style.css").read_text(encoding="utf-8")
    if "Content-Security-Policy" not in index or "script-src 'self'" not in index:
        raise RuntimeError("CSP segura ausente do renderer.")
    if re.search(r"<(?:script|link)[^>]+https?://", index, flags=re.IGNORECASE):
        raise RuntimeError("Dependência remota encontrada na interface.")
    if re.search(r"\son[a-z]+\s*=", index + app, flags=re.IGNORECASE):
        raise RuntimeError("Handler inline encontrado na interface.")
    if "fonts.googleapis.com" in style:
        raise RuntimeError("Fonte remota encontrada na folha de estilos.")
    expected_fonts = {
        "Plus Jakarta Sans": "200 800",
        "Inter": "100 900",
        "JetBrains Mono": "100 800",
        "Cinzel": "400 900",
    }
    for family, weight in expected_fonts.items():
        declarations = re.findall(
            rf"@font-face\s*\{{[^}}]*font-family:\s*['\"]{re.escape(family)}['\"][^}}]*\}}",
            style,
            flags=re.DOTALL,
        )
        if len(declarations) != 1:
            raise RuntimeError(f"A fonte {family} deve ser declarada exatamente uma vez.")
        if not re.search(rf"font-weight:\s*{re.escape(weight)}\s*;", declarations[0]):
            raise RuntimeError(f"A faixa de pesos da fonte {family} foi alterada.")


if __name__ == "__main__":
    verify()
    print("WEB_ASSETS_OK")
