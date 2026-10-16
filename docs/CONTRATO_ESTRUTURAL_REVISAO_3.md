# Contrato estrutural: revisão 3 do schema v2

Extensão compatível do [contrato v2](CONTRATO_ESTRUTURAL_V2.md):
`schema_version` continua 2 e `structure_revision` passa a 3. Os campos antigos
permanecem disponíveis. A correção de hierarquia pode mudar IDs cujo caminho
ancestral estava errado. Consumidores não devem persistir IDs sem a identidade
do documento.

## Representação comum

A representação canônica é o Markdown final acompanhado do índice de seções,
questões, respostas e blocos. A preparação editorial decide a estrutura uma vez.
JSON e HTML recebem exatamente o mesmo índice; o HTML incorpora o Markdown
integral e verifica os títulos contra linha, texto e nível antes de usar IDs.
Não há reconstrução independente da hierarquia em cada exportador.

`blocks` é a projeção ordenada dos trechos com conteúdo. Cada bloco informa:

- `id`, `parent_id`, `section_id`, `order`, `type`, `markdown`, `text` e `role`;
- `line_start`, `line_end`, `page_start`, `page_end`, também agrupados em `source`;
- `classification.status` e `classification.reasons` para ambiguidades.

Tipos observáveis: `heading`, `paragraph`, `list_item`, `quotation`,
`legal_transcription`, `footnote`, `note`, `table`, `visual`, `code`,
`question_stem`, `alternative` e `comment`. São classificações conservadoras,
não garantias de interpretação editorial. Estilo isolado não prova que um
parágrafo deva ser seção: `emphasis_without_confirmed_structure` pede revisão.
Um enunciado pode conter blocos de lista ou citação; não são reclassificados
automaticamente como alternativas. Comentários são vinculados ao cabeçalho de
resposta, sem interpretação jurídica.

`markdown` preserva o trecho literal do arquivo final. `text` é uma projeção
textual que retira estilos inline e comentários; pode manter marcadores de bloco
Markdown. Metadados de origem permanecem em `source_file`, `source_sha256`,
`page_coverage` e nas coordenadas; decisões editoriais em `sections`, `questions`,
`answers`, `blocks`; resultados de verificações em `structural_validation`,
`toc_validation`, `checks` e `editorial_issues`.

## Coordenadas e identidade

Linhas são 1-based e inclusivas do Markdown final. Páginas são físicas,
1-based e inclusivas. `null` indica origem desconhecida. Marcadores de página
inline preservam a origem dos dois lados de uma continuação; não atribuem todo
o parágrafo à última página. Não há alinhamento de cada palavra a coordenadas
PDF. Separadores, linhas vazias e comentários de proveniência isolados não
geram blocos de conteúdo.

Seções incluem descendentes. `direct_line_end` continua delimitando apenas o
preâmbulo direto, conforme v2. Para todos os trechos diretos descontínuos, use
os blocos cujo `section_id` é a seção desejada. Alternativas são separadas antes
da publicação do Markdown: seu fim inclusivo não inclui espaços ou o começo da
seguinte. Uma alternativa pode atravessar páginas e conter vários parágrafos.

Todos os IDs são locais ao documento. `document_identity.document_id` é o
SHA-256 do PDF, ou `null` sem fonte disponível. `version_id` é o SHA-256 dos
bytes UTF-8 do Markdown final. A chave entre documentos é `(document_id, id)`;
uma referência a uma versão exata inclui também `version_id`.

IDs de seções mantêm a política v2. Blocos de título reutilizam a identidade da
seção; questões e respostas também a referenciam. Alternativas usam o ID da
questão e a letra observada. Demais blocos usam SHA-256 truncado do pai, tipo
sintático, projeção textual e ocorrência entre homônimos. Parágrafos recebem
identidade antes da atribuição dos papéis de enunciado/comentário.
Alterar conteúdo/pai ou inserir
homônimo anterior pode alterar a identidade. Não há promessa de estabilidade
semântica entre revisões editoriais, renomeações ou PDFs diferentes.

## Reconhecimento e diagnósticos

Sequências alfabéticas são avaliadas com vizinhos, inclusive caracteres que
também são romanos. Tipos de marcador continuam podendo aninhar conforme a
pilha editorial existente. Enumerações locais misturadas entre lista, parágrafo
e título recebem tratamento equivalente; não se promove toda enumeração.

Evidência da fonte inclui posição de linha, limites da área de conteúdo, tamanho,
fonte e negrito. Rótulos numerados destacados podem ser separados da explicação
com confirmação na fonte. Títulos sem numeração exigem linha curta destacada na
fonte e estilo recorrente também observado em títulos numerados. Isso ainda é
heurístico. O PDF é protegido em memória antes dessa leitura adicional.

Alternativas aceitam `(A)`, `A)` e `a)`, inclusive sem espaço após o marcador.
Exigem sequência observada A, B, ...; não há tamanho fixo nem alternativa
inventada. Uma nova sequência A pode delimitar as respostas após enumeração
interna do enunciado. Saltos, repetições ou marcadores incorporados que destruam
a sequência deixam o conteúdo intacto e geram diagnóstico. Esse método não
resolve toda enumeração interna ambígua.

`structural_validation.status` distingue `passed`, `failed` e `inconclusive`.
Verifica unicidade, pais, progressão de níveis/linhas que impede ciclos,
contenção, limites diretos e sobreposição entre seções/alternativas irmãs.
Ambiguidades de classificação ou de resposta tornam o resultado inconclusivo.
Não comprova que todos os títulos reais foram encontrados.

`toc_entries.status`: `matched`, `source_discrepancy`, `unresolved`, `ambiguous`.
Pontilhados intercalados com espaços são normalizados somente na comparação.
Entradas ambíguas fornecem `candidate_ids`. `toc_validation` resume contagens:
`not_run` sem entradas reconhecidas, `inconclusive` com associações pendentes,
`failed` com divergências confirmadas da fonte, `passed` quando as entradas
reconhecidas correspondem. Não garante que todo sumário da fonte foi detectado.

As verificações visual e jurídica continuam `not_run` nos artefatos automáticos.
Auditorias humanas posteriores pertencem ao relatório de validação. Scores
lexicais, extração sem erro e invariantes aprovados não provam fidelidade visual,
semântica ou jurídica. A política de privacidade, OCR e omissão visual permanece
a do contrato v2.

## Revisões editoriais explícitas

O exportador aceita opcionalmente `editorial_review`. A revisão deve declarar
os hashes exatos do PDF e do Markdown e identificar cada bloco por ID, hash do
conteúdo, página e bounding box da evidência. Todas as entradas são verificadas
antes de alterar qualquer classificação. A conferência desses campos estabelece
correspondência de versão; não autentica autoria nem comprova o parecer.

Blocos revistos recebem `classification.status: reviewed`, decisão, justificativa,
evidência e `method: source_review`. O resultado original do detector permanece
em `automatic_status` e `automatic_reasons`. A função editorial é acrescentada
em `role.editorial_role`, sem promover automaticamente o bloco a seção.

O campo exportado `editorial_review` informa `not_run`, `partial` ou `completed`,
contagens, hashes e problemas da fonte. `completed` significa que todos os
blocos ambíguos dessa versão receberam decisão, não que todo o documento foi
certificado. Reconversões não carregam essas decisões automaticamente. O método
de inspeção e seus limites devem constar no relatório associado.

## Caixas de nota e prefixos das alternativas

No perfil `curso`, um rótulo isolado `ATENÇÃO!` pode ser agrupado com os
parágrafos seguintes quando uma caixa fechada da mesma página do PDF atesta
todo o conteúdo. São reconhecidas bordas retangulares inteiras ou compostas
por quatro segmentos/faixas. A geometria é lida antes da proteção de rodapés;
o texto é lido somente depois da proteção. Sem bordas fechadas, correspondência
completa ou continuidade na mesma página, o agrupamento não é presumido.
O contador `source_note_boxes_grouped` registra os agrupamentos aplicados.

Dentro de uma questão com sequência de alternativas A/B/... confirmada,
um prefixo de lista na mesma linha de uma alternativa, como `- (B)`, é retirado
ao separar essa alternativa. Isso evita criar um item vazio. Não remove hífens
de outras listas, comentários ou sequências ambíguas. O contador
`alternative_list_prefixes_removed` registra os prefixos retirados.

## Reconstrução geral das tabelas da fonte

A etapa `source_table_formatting` ocorre antes dos perfis de títulos, tanto em
`curso` quanto em `jurisprudencia`. Não contém nomes, números de apostila ou
substituições de expressões dos materiais de validação.

Somente tabelas Markdown cujo texto corresponda integralmente, por coluna e na
mesma página, a uma única tabela delimitada por bordas vetoriais são refeitas.
A comparação tolera espaços, estilos e hifenização de linha; preserva pontuação
numérica, evitando confundir valores como `1,23` e `12,3`. Sem correspondência
única, o conteúdo extraído permanece intacto. Correspondências múltiplas são
contadas em `source_tables_ambiguous`.

As células são lidas da cópia protegida do PDF e reconstituídas com espaços,
negrito, itálico e sublinhado atestado geometricamente. Linhas comuns são
refluídas; rótulos e enumerações preservam quebras internas. A primeira linha
não se torna cabeçalho por mera posição: sem evidência tipográfica de cabeçalho,
uma linha Markdown vazia representa essa ausência e é retirada do HTML renderizado.

Continuações exigem páginas consecutivas, bordas de colunas compatíveis, posição
no fim/início das páginas, primeira célula vazia na continuação e ausência de
texto independente entre as partes. O marcador de página é preservado dentro
da célula continuada, mantendo o intervalo do bloco. Tabelas com células mescladas
ou geometria rotacionada são preservadas sem reconstrução automática.

O detector usa somente a geometria vetorial nesta etapa, independentemente da
ativação do modelo opcional de layout. `source_tables_rebuilt` conta as partes
refeitas; `source_table_continuations_joined`, as uniões. Esses contadores e os
testes de DOM não substituem a conferência visual da impressão.
