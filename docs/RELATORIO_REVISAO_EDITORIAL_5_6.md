# Revisão editorial das apostilas 5 e 6

Continuação da [correção estrutural](RELATORIO_ESTRUTURA_GERAL.md), em 15/10/2026.
Os artefatos anteriores permanecem em `PDFs Convertidos/estrutura-final/`.
Os revisados estão em `PDFs Convertidos/estrutura-revisada/`.

## Revisão dos 53 casos

Foram examinados os 25 blocos sinalizados da apostila 5 e os 28 da apostila 6.
Cada decisão foi confrontada com texto, posição e recorte visual protegido da
página correspondente. A localização textual dos 53 recortes correspondeu
integralmente ao texto da fonte após normalização para localização. Essa
correspondência serve para localizar a evidência; não certifica a classificação.

| Tratamento | Apostila 5 | Apostila 6 |
|---|---:|---:|
| Subtítulo alfanumérico recuperado | 4 | 0 |
| Continuação unida ao parágrafo | 1 | 0 |
| Rótulo editorial de lista, tabela, nota ou transcrição | 5 | 8 |
| Frase de destaque preservada no corpo | 3 | 5 |
| Chamada de julgado/tese com função local | 8 | 13 |
| Referência processual/bibliográfica | 4 | 1 |
| Comando final do enunciado | 0 | 1 |
| Total | 25 | 28 |

As quatro subseções alfanuméricas da apostila 5, nas páginas 36, 38 e 41,
passaram a títulos filhos do tópico de letra correspondente. A regra aceita
prefixos como `A.1` e `A.2`, retorna ao pai correto após uma enumeração interna
e rejeita promover um rótulo seguido de explicação extensa como título inteiro.

A palavra final isolada na página 86 foi reunida à frase anterior. A nova regra
exige adjacência atestada entre linhas do mesmo bloco PDF; sem essa evidência,
preserva a separação. A sequência de palavras dos Markdown revisados é idêntica
à dos anteriores, removendo apenas os marcadores de título para a comparação.
Isso verifica conservação desta revisão, não fidelidade integral ao PDF.

Na fonte há duas subseções diferentes com marcador `A.1`, nas páginas 36 e 38.
Ambas foram mantidas, com IDs distintos e diagnóstico `source_numbering_repeated`.
Não foi renumerado o documento por inferência. As três divergências de páginas
do sumário da apostila 5 também permanecem registradas.

## Rastreabilidade e limites da revisão

Os 48 casos preservados no corpo possuem `classification.status: reviewed`,
papel em `role.editorial_role`, justificativa e evidência com página/bounding box.
`automatic_status` e `automatic_reasons` conservam o resultado anterior do
detector. Portanto, a revisão não é apresentada como melhoria do reconhecimento
automático de todos esses casos.

`document_review.py` somente aplica decisões explícitas compatíveis com hash do
PDF, hash do Markdown, ID, conteúdo do bloco e página. Uma revisão obsoleta ou
incompatível falha antes de alterar as classificações. Não há carregamento
automático de exceções textuais no conversor. Reconversões diferentes exigem
nova conferência; a validação dos hashes não prova o acerto do parecer editorial.

O resultado dos invariantes é `passed` em ambos os artefatos revisados, e não há
bloco anteriormente ambíguo sem decisão. Esse estado tem escopo de árvore,
intervalos e decisões registradas. Não é aprovação jurídica ou visual integral.

Arquivos privados de auditoria:

- `docs/fidelity_audit_tmp/editorial-review/baseline-5.json` e `baseline-6.json`:
  lista dos casos originais e localização dos recortes.
- `audit-5.json` e `audit-6.json`, no mesmo diretório: decisão de cada um dos
  53 casos, incluindo os cinco resolvidos por correção de estrutura/continuidade.
- Dez folhas `review-5-*.png` e `review-6-*.png`: recortes protegidos efetivamente
  examinados nesta revisão assistida. Não houve inspeção humana independente.
- Na pasta de entrega: dois arquivos `.editorial-review.json` vinculados às
  versões exatas, e `revisao-editorial.json` com o resumo.

## Validação dos HTMLs

Os dois HTMLs foram regenerados a partir dos Markdown e dos índices revisados.
O teste em JSDOM confirmou 137 títulos/âncoras na apostila 5 e 72 na apostila 6,
todos com alvos existentes, sem erros de execução. Os payloads correspondem aos
JSON e Markdown externos. A nova aplicação da proteção textual aos três formatos
e aos registros de revisão não exigiu substituições.

**Inspeção visual em navegador: bloqueada.** A restrição da sessão anterior
recusou `file:` e proibiu contornar o bloqueio por rota ou superfície alternativa.
A consulta às abas disponíveis não encontrou os HTMLs em URL autorizada. Foi
solicitada uma URL HTTP(S) autorizada ou liberação da visualização local.
Não foi aberto servidor nem usada outra rota para contornar essa restrição.

Assim, não foram certificados aparência, responsividade, rolagem ou navegação
visual no navegador. Os campos automáticos de fidelidade visual continuam
`not_run`. A inspeção dos recortes PDF não substitui essa etapa.

## Validações de código

Foram executados os testes sintéticos de subtópicos alfanuméricos, continuação
atestada e aplicação segura da revisão; as regressões privadas também passaram.
O resultado consolidado da suíte e os comandos finais estão nos logs em
`docs/fidelity_audit_tmp/editorial-review/`.

- Suíte Python completa com os artefatos revisados: **358 testes aprovados**.
- Suíte JavaScript: **11 testes aprovados**.
- `ruff check .` e `git diff --check`: aprovados.
- `node scripts/verify-structured-exports.js "PDFs Convertidos/estrutura-revisada"`:
  dois conjuntos aprovados, com 209 âncoras no total.

Não foram adicionadas dependências, serviços ou exceções baseadas em frases das
apostilas. As alterações anteriores do working tree foram preservadas.

## Captura de tela posteriormente fornecida pelo usuário

Foi recebida uma captura de 1763 × 40511 pixels do HTML revisado da apostila 5.
O arquivo original foi preservado com hash em
`docs/fidelity_audit_tmp/editorial-review/user-screenshot/`, junto dos recortes
examinados e de `screenshot-review.json`. A revisão dos pixels fornecidos pelo
usuário não depende do acesso automatizado ao navegador.

A imagem termina em conteúdo da página física 30; portanto, não documenta as
94 páginas completas. Foram examinados cinco recortes em resolução legível,
com início vertical em 0, 4000, 19000, 30000 e 38711 pixels. Nesses recortes,
texto, destaques e tabela estão legíveis, sem sobreposição aparente. O mapa de
leitura e os avisos de privacidade/divergências da fonte aparecem na captura.

**Foi confirmado um defeito visual na página física 16:** uma caixa contém
somente o rótulo `ATENÇÃO!`, deixando as duas súmulas seguintes fora dela. A
comparação com a página PDF protegida confirmou que os três elementos pertencem
à mesma caixa na fonte. A redação está presente, mas o agrupamento visual não
foi preservado. Este achado permanece pendente de correção e de nova conferência.

Estado desta etapa: **validação visual parcial, com defeito confirmado**.
Ainda faltam evidências do restante da apostila 5 e da apostila 6. Uma captura
estática também não comprova cliques, rolagem interativa, impressão ou
responsividade. Não foram alterados os estados automáticos de fidelidade visual
dos artefatos nem declarada aprovação visual integral.

## Impressões completas posteriormente fornecidas

As impressões das apostilas 5 e 6 foram recebidas e examinadas: 135 e 102 páginas,
respectivamente. O resultado e as evidências estão em
[RELATORIO_VALIDACAO_IMPRESSA_5_6.md](RELATORIO_VALIDACAO_IMPRESSA_5_6.md).
A inspeção confirmou o defeito de agrupamento da caixa da apostila 5 e quatro
marcadores vazios entre alternativas na apostila 6. Também demonstrou que a
impressão da apostila 6 não corresponde à estrutura da revisão atual nos trechos
de vício formal/material. A aprovação visual continua pendente; a ausência de
evidências completas da impressão, registrada na seção anterior, foi suprida
pelos PDFs, com essa limitação de versão.

Os dois defeitos foram posteriormente corrigidos, com regeneração dos formatos
revisados e preservação das 48 decisões editoriais. A seção final do relatório
de validação impressa registra a entrega pronta para nova impressão, os hashes
e as verificações: 363 testes Python, 11 JavaScript e 209 âncoras válidas.
A aprovação visual final depende da nova impressão dos HTMLs regenerados.
