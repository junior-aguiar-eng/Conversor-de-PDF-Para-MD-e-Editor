# Fase 7 — Dependências, build e código residual

## Auditoria conservadora de dependências

A varredura cobriu imports estáticos, imports condicionais, coleta explícita do PyInstaller e a árvore produzida por `uv tree`.

| Dependência direta | Evidência atual | Decisão |
|---|---|---|
| `cryptography` | assinatura e validação Ed25519; emissor administrativo | manter |
| `deep-translator` | tradução online em `web_api.py` | manter |
| `edge-tts` | síntese de voz em `web_api.py` | manter |
| `pymupdf` | leitura, edição, renderização, acervo e OCR | manter |
| `pymupdf4llm` | conversão principal; import condicional em `converter.py` | manter |
| `pywebview` | interface nativa e diálogos do sistema | manter |
| `rapidocr-onnxruntime` | OCR local carregado condicionalmente | manter |
| `openpyxl` | nenhum consumidor direto confirmado | manter pendente de experimento individual |
| `pdfplumber` | nenhum consumidor direto confirmado | manter pendente de experimento individual |
| `pypdf` | nenhum consumidor direto confirmado | manter pendente de experimento individual |
| `pytesseract` | nenhum consumidor direto confirmado no fluxo atual | manter pendente de experimento individual |
| `python-docx` | nenhum consumidor direto confirmado | manter pendente de experimento individual |

Nenhuma dependência foi removida. Para cada candidata ainda será necessário, isoladamente: reconstruir o lock sem ela, medir instalação, gerar release limpa, validar inicialização e executar a funcionalidade historicamente associada. A ausência de import direto, sozinha, não autoriza remoção.

A release de referência também foi inspecionada: `openpyxl`, `pdfplumber`, `pypdf`, `pytesseract` e `docx` não foram incluídos pelo PyInstaller. Isso indica que a eventual remoção reduziria principalmente o ambiente-fonte/tempo de instalação, não a release atual; não substitui o experimento individual exigido para removê-las.

## Build unificado

- `build_release.ps1` permanece o comando oficial e resolve a raiz por `$PSScriptRoot`.
- `build_app.py` permanece como wrapper compatível.
- toda a preparação, configuração do PyInstaller e validação pós-build está em `build_support.py`.
- o fluxo oficial usa `uv run --frozen --group dev`; `pyinstaller==6.22.2` está fixado no projeto e no lock.
- os caminhos entregues ao PyInstaller são absolutos, portanto o chamador pode estar em qualquer diretório.

## Limpeza conservadora

- removido apenas o campo de instância `this.zoom`, depois de confirmar que não possuía consumidor; o zoom por documento foi preservado.
- `deactivate_software()` permanece disponível para testes e suporte.
- os formatos administrativos e a chave pública embarcada não foram alterados.
- PEM administrativo criptografado passou a ser opcional; PEM sem senha continua legível.
- cada família tipográfica continua declarada uma única vez, com os mesmos arquivos variáveis, pesos e identidade visual.
