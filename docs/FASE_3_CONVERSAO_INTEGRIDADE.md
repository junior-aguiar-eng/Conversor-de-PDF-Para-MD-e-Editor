# Fase 3 — Conversão, OCR e integridade das saídas

## Implementação

- O coordenador reserva, antes de criar workers, nomes exclusivos e alinhados para Markdown, imagens e partes. PDFs homônimos recebem sufixos `(2)`, `(3)` etc.; nenhuma decisão de destino fica a cargo do worker.
- A conversão ocorre página a página, até o limite preservado de 1.000 páginas, com estados `native`, `ocr`, `fallback`, `empty` e `failed`.
- Falha isolada produz marcador `NEXOJURIS_PAGE`, aviso visível no Markdown e relação de páginas problemáticas no evento e no resumo do lote, sem descartar páginas recuperadas.
- A interface permite reprocessar somente as páginas falhas. A tentativa gera saída separada, identificada por `páginas reprocessadas`, sem substituir o documento anterior.
- A divisão oferece modo semântico, padrão recomendado, e modo estrito. Tabelas, imagens isoladas e blocos de código cercados são unidades indivisíveis; o modo estrito só corta texto comum como último recurso.
- Markdown, diretório de partes e imagens são preparados em artefatos temporários e promovidos por renomeação atômica. Uma falha remove somente temporários e destinos promovidos pela tentativa corrente.

## Garantias verificadas

- lote com PDFs homônimos não compartilha Markdown, diretório de imagens ou diretório de partes;
- documento de 1.000 páginas continua integralmente processável;
- páginas recuperadas sobrevivem à falha de outra página;
- reprocessamento limita a extração às páginas solicitadas;
- falha durante a promoção transacional preserva arquivos alheios;
- divisões não fragmentam silenciosamente tabela, imagem ou bloco de código.
