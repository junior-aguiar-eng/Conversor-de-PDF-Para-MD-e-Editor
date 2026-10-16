# Correção geral de formatação de tabelas

Data: 16/10/2026. Estado: implementada, verificada e aplicada aos arquivos
revisados. A nova impressão ainda precisa de conferência visual.

## Alteração

A nova etapa `source_table_formatting.py` atua antes dos perfis `curso` e
`jurisprudencia`, no fluxo comum de `finalize_markdown`. A correção usa bordas,
coordenadas de células e caracteres da fonte. Não contém frases de apostilas,
nomes de matérias, números de documentos ou substituições de palavras específicas.

Uma tabela só é reconstruída quando cada coluna corresponde integralmente a
uma única tabela da mesma página. A comparação tolera espaços, estilos e
hifenização de linha, mas distingue pontuação numérica. Os caracteres das células
vêm da cópia do PDF protegida pela política existente de privacidade.

Isso restaura espaços junto de trechos destacados, reconstitui palavras quebradas
entre linhas da mesma célula e preserva negrito, itálico e sublinhado atestado
pelas linhas da fonte. Texto comum pode refluir; enumerações e rótulos mantêm
quebras internas. O conteúdo inicial não é promovido a cabeçalho apenas por ser
a primeira linha. O leitor HTML remove somente o cabeçalho vazio usado pelo
Markdown para representar tabelas sem cabeçalho identificado.

Continuações exigem páginas consecutivas, colunas alinhadas, posição no fim/início
das páginas, ausência de texto independente no intervalo e célula inicial vazia
na continuação. O marcador de página permanece dentro da célula continuada, e o
preparador editorial mantém esse marcador inline sem quebrar a tabela.

O detector geométrico foi isolado do modelo opcional de layout do PyMuPDF na
instância privada da página. Nenhuma opção global do extrator é alterada.

## Verificação

- **372 testes Python aprovados**, incluindo o corpus geral de fidelidade, os
  novos testes sintéticos e as regressões privadas das duas apostilas.
- **11 testes JavaScript aprovados**; o teste do leitor verifica tabelas sem
  cabeçalho artificial e preservação do itálico dentro das células.
- `ruff check .` e `git diff --check`: aprovados.
- Exportações finais verificadas em JSDOM: **209 títulos/âncoras válidos**,
  payloads correspondentes aos JSON/Markdown e zero itens de lista vazios.
- As verificações dirigidas confirmaram espaços nas células afetadas, uma única
  tabela para o quadro continuado, duas linhas de corpo sem cabeçalho espúrio e
  os dois termos em itálico. As correções anteriores de notas e alternativas
  continuam presentes.

Os testes sintéticos usam outros vocabulários e verificam os dois perfis,
idempotência, preservação de cabeçalho real, sublinhado, privacidade, exemplos em
blocos de código, correspondências duplicadas, valores divergentes e vírgula
decimal. Um teste reproduz a interferência do detector opcional de layout.
Parágrafos intermediários impedem a união de tabelas independentes.

Logs e cópias anteriores estão em `docs/fidelity_audit_tmp/editorial-review/`.
O log final da suíte é `table-python-final.log`; a regeneração final está em
`table-regeneration-release.log`. A execução intermediária que detectou a
interferência de layout foi corrigida antes da aprovação da suíte final.

## Arquivos regenerados

Em `PDFs Convertidos/estrutura-revisada/`, foram atualizados os Markdown, JSON,
HTML e registros editoriais das apostilas 5 e 6. A regra reconstruiu duas partes
de tabela na 5 e três na 6, unindo uma continuação. Foram preservados os
137/72 títulos, 31/18 questões, 31/18 respostas e as 20/28 decisões editoriais
de blocos cujo conteúdo e páginas permaneceram idênticos.

A comparação dos Markdown confirmou que todo o texto visível fora das tabelas
permaneceu idêntico. Os hashes atuais estão em `impressao-revisada.json`, e
`IMPRESSAO-REVISADA.txt` orienta a nova impressão. Os PDFs enviados pelo usuário
e os PDFs-fonte não foram sobrescritos. Não foram feitos commit, push ou release.

## Limites

Sem correspondência única, a tabela extraída é preservada. Células mescladas,
geometria rotacionada e tabelas sem bordas reconhecíveis não são reconstruídas
por esta regra. Isso evita inventar células ou valores quando falta evidência;
a regra é geral, mas não certifica automaticamente qualquer PDF.

Os testes de conteúdo e DOM não comprovam paginação, cortes ou aparência final
da nova impressão. A validação visual dos HTMLs regenerados permanece pendente
dos novos PDFs, sem promover os estados automáticos de fidelidade a aprovação.
