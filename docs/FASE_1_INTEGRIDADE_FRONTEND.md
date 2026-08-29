# Fase 1 — Bloqueadores funcionais e integridade documental

## Implementação

- `escapeHtml()` passou a ser a função central de escaping para fila, marcadores, busca e dropdowns. Logs usam nós DOM e `textContent`, sem interpolar mensagens em `innerHTML`. O destaque de busca admite somente a tag `mark` com a classe produzida pelo backend.
- cada PDF possui estado próprio, identificado pelo caminho canônico entregue pelo backend: páginas, cache, anotações pendentes, pilha de desfazer, senha da sessão, página, zoom, marcadores e metadados;
- ao trocar de documento com anotações pendentes, a interface exige uma decisão explícita: salvar, manter rascunho, descartar ou cancelar. O rascunho é o comportamento visualmente recomendado e permanece apenas em memória;
- carregamentos de documento e renderizações de página recebem identificadores monotônicos. Respostas e callbacks de imagem obsoletos não alteram documento, canvas, cache ou metadados atuais;
- inicialização da bridge, inicialização pós-termos e bindings do leitor são idempotentes.

## Evidência de saída

O ensaio JavaScript `tests/phase1_frontend.test.js` executa os renderizadores relevantes com payloads hostis, todas as decisões de troca, restauração de dois rascunhos independentes, página, zoom, senha, desfazer e cache por documento, corridas de respostas de documentos e páginas e chamadas concorrentes de inicialização.

O critério de saída foi satisfeito de forma sintética: alternar repetidamente entre dois PDFs não mistura páginas, anotações, senhas, cache ou metadados. Nenhum comportamento de conversão, OCR, edição ou licenciamento foi removido ou limitado.
