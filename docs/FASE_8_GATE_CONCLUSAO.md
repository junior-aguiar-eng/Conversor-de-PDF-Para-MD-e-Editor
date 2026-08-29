# Fase 8 — Gate integrado de conclusão

Execução em 29 de agosto de 2026, sobre a release `v1.3.2` gerada pelo fluxo oficial.

| Gate | Evidência | Resultado |
|---|---|---|
| suíte e regressões | `python -m unittest discover -s tests` — 141 testes | aprovado |
| lint Python | `uv run ruff check .` | aprovado |
| JavaScript | `node --check web/app.js`, `phase1_frontend.test.js` e `phase2_renderer.test.js` | aprovado |
| assets autocontidos | `scripts/verify_web_assets.py`; CSP e fontes locais únicas | aprovado |
| visual do frontend | release real inspecionada em WebView2: termos, conversor e gate de licença sem quebra de layout | aprovado |
| interface offline | release iniciada por 8 segundos sem dependência remota | aprovado |
| build limpo | `build_release.ps1` chamado fora da raiz; PyInstaller 6.22.2 / Python 3.14.6 | aprovado |
| instalação nova | cópia temporária de 379.843.091 bytes; SHA-256 do executável idêntico | aprovado |
| reinício/persistência | aceite criado somente na instalação descartável e preservado após fechar/reabrir | aprovado |
| 1.000 páginas | `test_exactly_one_thousand_synthetic_pages_remain_convertible_in_full` | aprovado |
| lote homônimo | `test_batch_reserves_distinct_destinations_for_homonymous_inputs` | aprovado |
| PDF híbrido/OCR localizado | baseline híbrida e `test_page_coverage_preserves_successes_and_marks_isolated_failure` | aprovado |
| troca com anotações | regressões JavaScript de cancelar, descartar e salvar | aprovado |
| Markdown legítimo/malicioso | baseline funcional e `phase2_renderer.test.js` | aprovado |
| licenças legadas | ACT2/NXJ e migração cobertos; HMAC anterior rejeitado explicitamente | aprovado |
| arquivo indisponível | estado preservado e relocação coberta pela Fase 4 | aprovado |
| tradução/TTS | blocos e erros offline testados; chamadas reais com texto genérico retornaram tradução e áudio | aprovado |
| dados reais | nenhum `.db`, `.sig` ou `.pem` apareceu no worktree; banco temporário do ensaio foi removido com a instalação | aprovado |
| árvore Git limpa | alterações das Fases 6–8 ainda aguardam autorização independente para commit | pendente de publicação |

## Observações não bloqueantes

- PyMuPDF mantém aviso de futura remoção do alias `fitz`.
- `caniuse-lite` informa base de navegadores desatualizada, sem falha de build.
- A coleta do PyInstaller registra imports opcionais ausentes para Android, `onnx` e tabelas do `pycparser`; a release foi concluída, validada e iniciada.
- As cinco dependências sem consumidor direto permanecem instaladas. Remoção somente após auditoria individual com comparação de instalação, release e funcionalidade, conforme a Fase 7.
