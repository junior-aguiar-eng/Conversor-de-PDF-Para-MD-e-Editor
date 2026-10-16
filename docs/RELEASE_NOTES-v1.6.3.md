# NexoJuris Conversor v1.6.3

Corrige a reconstrução editorial e a formatação dos materiais convertidos com
regras gerais baseadas no contexto e na geometria da fonte, sem exceções por apostila.

- Reconstrói títulos, questões, alternativas, gabaritos e notas de continuação,
  evitando confundir enumerações internas com novas seções ou questões.
- Recupera quadros, células e separadores a partir do PDF e recompõe linhas e
  endereços quebrados sem completar conteúdo ausente.
- Mantém âncoras e navegação consistentes entre Markdown, JSON e HTML; expõe
  diagnósticos editoriais para revisão dos casos ambíguos.
- Corrige a impressão dos seis níveis de título, reduzindo títulos isolados no
  fim da página, e inclui os novos módulos nos manifestos de distribuição.

O JSON mantém `schema_version: 2` e passa a `structure_revision: 3`.
Reconversões podem alterar IDs derivados da estrutura; consumidores que persistem
esses IDs devem reindexar os documentos reconvertidos.

A revisão dos dois materiais de referência cobriu 241 páginas impressas,
209 títulos, 49 questões e seus 49 gabaritos. O relatório consolidado registra
os casos e as verificações; esses resultados não certificam qualquer PDF futuro.
Os PDFs, HTMLs privados e os logs brutos não fazem parte da distribuição pública.

O instalador Windows inclui o runtime do conversor e não inclui o Admin nem
chaves privadas. A assinatura Authenticode permanece ausente (`NotSigned`).
O SHA-256 e o relatório público de validação acompanham os arquivos da release.

Referências: [contrato da revisão 3](CONTRATO_ESTRUTURAL_REVISAO_3.md),
[correção consolidada](RELATORIO_CORRECAO_CONSOLIDADA_5_6.md) e
[distribuição Windows](DISTRIBUICAO_WINDOWS.md).
