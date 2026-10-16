# Revisão consolidada das apostilas 5 e 6

> Fechamento posterior em 16/10/2026: as pendências abaixo foram corrigidas por regras gerais, os formatos foram regenerados e a validação no Edge/A4 foi concluída. Consulte [a entrega e suas evidências](RELATORIO_CORRECAO_CONSOLIDADA_5_6.md). O texto abaixo preserva o inventário anterior à implementação.

Data: 16/10/2026. **Revisão concluída com pendências; materiais ainda não aprovados para impressão definitiva.**

A conclusão anterior, que registrava apenas uma ressalva tipográfica na enumeração de uma tabela, foi estreita demais. Esta revisão amplia a comparação entre fonte, estrutura exportada e impressão e substitui aquela conclusão. Não houve alteração de código nem regeneração dos materiais nesta rodada.

## Material e cobertura

Foram utilizadas as fontes `Desktop/MEGE/direito_constitucional5.pdf` e `direito_constitucional6.pdf`, os respectivos Markdown/JSON/HTML em `PDFs Convertidos/estrutura-revisada` e as impressões entregues pelo usuário:

| Material | Páginas impressas | SHA-256 da impressão |
|---|---:|---|
| Apostila 5 | 135 | `eebb304e0ea302440b5278addc1ce70c7b654064f55e3853565b84e394b2f952` |
| Apostila 6 | 102 | `34703eb6b1a06d158bac5a2798f85c11b72ebaf742dfdc6f4a244e9d4afd038e` |

A inspeção geral das 237 páginas em 13 folhas de visão geral foi complementada por conferência ampliada de quadros da fonte, comparação textual contextual, estrutura dos 67 blocos de nota e recortes das quatro transições de página problemáticas. Os textos das fontes foram protegidos antes da extração/renderização; somente a geometria foi capturada antes da proteção.

As verificações estruturais executadas nesta rodada confirmaram 137 + 72 títulos/âncoras, sincronismo de Markdown/JSON/payload HTML, ausência de itens de lista vazios e os casos anteriormente corrigidos de tabelas, notas e alternativas. Existem 31 + 18 questões e 31 + 18 respostas. Esses resultados não equivalem a aprovação editorial de cada palavra nem a revisão jurídica do conteúdo. A inspeção visual utilizou os PDFs impressos, sem nova validação no navegador.

## 1. Limites de notas e caixas de destaque

**23 notas ATENÇÃO têm enquadramento incompleto**: parte do conteúdo que pertence à caixa na fonte aparece como corpo comum na saída. O problema é de agrupamento; os trechos examinados não foram simplesmente omitidos.

| Apostila | Páginas físicas da fonte, uma entrada por caixa |
|---|---|
| 5 — 14 caixas | 8, 8, 9, 13, 14, 15, 20, 22, 38, 40, 45, 51, 54, 59 |
| 6 — 9 caixas | 8, 14, 17, 23, 30, 32, 35, 43, 52 |

**Outras dez caixas OBSERVAÇÃO aparecem como parágrafos comuns**, sem representação do agrupamento original:

| Apostila | Páginas físicas da fonte |
|---|---|
| 5 — 3 caixas | 20, 38, 57 |
| 6 — 7 caixas | 10, 12, 20, 20, 33, 42, 45 |

A evidência inclui os limites vetoriais fechados e o texto contido neles. A observação da fonte 6, página 10, por exemplo, abrange três parágrafos. Reconhecer apenas seu primeiro parágrafo não resolve a estrutura.

**Causa no código:** `read_source_evidence` registra caixas de nota somente quando a primeira linha é exclusivamente `ATENÇÃO!`. `_group_source_notes` depende dessa evidência. A regra deixa de cobrir o rótulo acompanhado de texto na mesma linha e outros rótulos editoriais. Algumas caixas também continuam na página seguinte.

**Critério geral para a correção:** usar limites e sequência textual da caixa, permitindo múltiplos parágrafos, rótulos diversos e continuação entre páginas quando comprovada pela fonte. Não transformar toda ocorrência dessas palavras em nota, nem absorver conteúdo externo à caixa. Preservar estilos, marcadores de origem e vínculo das revisões manuais.

## 2. Títulos separados do primeiro parágrafo na impressão

Quatro ocorrências confirmadas nos recortes do fim da página e início da seguinte:

| Apostila | Página impressa | Título |
|---|---:|---|
| 5 | 22 | A. Legitimidade ativa e passiva do mandado de segurança coletivo |
| 5 | 84 | B. Expulsão |
| 6 | 44 | 1.1.11.2. Transcendência dos motivos determinantes (importante diferenciar!) |
| 6 | 45 | 1.1.11.3. Objetivação do Recurso Extraordinário |

**Causa no código:** a regra de impressão em `web/reading-export.html` aplica `break-after: avoid` somente a `h1`–`h4`; os níveis `h5` e `h6` ficam descobertos. A correção deve abranger todos os níveis e ser verificada nas transições reais, considerando também títulos consecutivos.

A continuação `ii)`/`iii)` na tabela da apostila 6, página impressa 8, permanece uma ressalva menor: os itens ficam na mesma linha. Deve entrar no mesmo lote de ajustes de apresentação, com regra de enumeração dentro de célula, sem exceção pelo nome da apostila.

## 3. Separadores, palavras e hífens

Foram confirmadas **18 ocorrências de separadores inadequados em relação à fonte**, distribuídas abaixo. As páginas são físicas da fonte. A tabela é um inventário das ocorrências verificadas; não é uma promessa de detecção de toda alteração lexical possível.

| Apostila/página | Saída encontrada | Evidência/resultado esperado |
|---|---|---|
| 5/9 | `h abeas`; `corpu s` | A fonte traz `habeas corpus`, sem esses espaços internos. |
| 5/13, 14, 15 | `m andado` — três ocorrências | A fonte traz `mandado`. |
| 5/69 | `licençamaternidade` | A fonte separa `licença-` / `maternidade`. |
| 5/70 | `licençapaternidade`; `licençamaternidade` | Os hífens estão presentes na fonte. |
| 5/75 | `licençamaternidade` | A fonte traz `licença maternidade`, com espaço; não impor hífen por dicionário. |
| 6/12 | `criouse` | A fonte traz `criou-` / `se`. |
| 6/14 | `reeditála` | A fonte traz `reeditá-` / `la`. |
| 6/21 | `constitucionale` | A fonte separa `constitucional e`. |
| 6/22 | `de- têm` | A quebra da fonte é `de-` / `têm`: reconstruir `detêm`. |
| 6/32 | `ter‐ ceirização` | Hífen U+2010 seguido de quebra na fonte: reconstruir `terceirização`. |
| 6/34 | `c ontrole`; `incidental- difuso` | A fonte traz `controle incidental-difuso`. |
| 6/42 | `inconstituciona‐ lidade`; `inconsti‐ tucionalidade` | Duas palavras cortadas por hífen U+2010 na fonte. |

O problema atravessa trechos com marcação de estilo e diferentes caracteres de hífen. As rotinas atuais não cobrem todas essas formas de separação/reunião. Uma correção geral precisa alinhar o contexto visível com a fonte, preservar a marcação e distinguir hífen lexical, quebra de linha e espaço real. Substituições globais dessas palavras não são uma solução aceitável.

**URL a preservar:** na apostila 6, fonte página 19, o caminho `Notas-sobre-a-teoria-do-estado-` continua com `de-coisas-inconstitucional`. Na saída, aparece `estadode`. A fonte não contém anotação de link com URI para resolver a intenção de modo independente. Há divergência textual a tratar com cautela; não foi demonstrada falha HTTP. URLs precisam de tratamento próprio, sem aplicação automática de regras de palavras comuns.

**Não atribuir à conversão:** `manda- do` (5/76), `desvairada- O` (6/15), `deve- se` (6/42) e `pré‐ constitucional` (6/31) já apresentam esses separadores na fonte. Não devem ser corrigidos como regressões do conversor. Títulos extensos também não foram classificados como defeitos apenas pelo comprimento.

## Resultado e fechamento das pendências

Há três frentes de correção geral: **agrupamento por geometria da fonte; paginação de títulos/enumerações; reconstrução contextual de separadores com proteção de URLs**. As ocorrências acima devem ser tratadas juntas, preservando as alterações e revisões já existentes.

Antes de solicitar outra impressão ao usuário, o lote precisa ser implementado, validado contra esses trechos reais e casos genéricos, reconvertido e comparado novamente com a fonte. Os testes anteriores (372 Python e 11 JavaScript) continuam sendo evidência histórica da implementação anterior; não foram reexecutados nem usados como prova de aprovação visual nesta rodada.

Evidências locais detalhadas estão em `docs/fidelity_audit_tmp/editorial-review/consolidated-review/`: `source-audit.json`, `split-word-audit.json`, `layout-audit.json`, `other-callout-boxes.json`, imagens das caixas e recortes `orphan-*.png`. Essa pasta é temporária/ignorada pelo Git; este relatório mantém o inventário necessário fora dela.
