# Correção consolidada de formatação — apostilas 5 e 6

Data: 16/10/2026. **Implementada, regenerada e validada no Edge, inclusive em impressão A4.**

Esta entrega fecha as pendências inventariadas em [RELATORIO_REVISAO_CONSOLIDADA_5_6.md](RELATORIO_REVISAO_CONSOLIDADA_5_6.md). Não foi necessária outra impressão pelo usuário.

## Regras gerais implementadas

- **Notas e destaques:** reconhecimento por caixas fechadas da fonte protegida, rótulo com ou sem texto na mesma linha e múltiplos parágrafos. Continuações exigem geometria alinhada, páginas consecutivas e ausência de conteúdo independente no intervalo. Rótulos no fim da página podem introduzir uma caixa na página seguinte quando essa relação é comprovada. Tabelas dentro das notas preservam suas células e a ordem de leitura por coluna. O rastreamento de páginas é calculado antes do agrupamento, para não perder a origem das notas seguintes.
- **Espaços e hífens:** comparação do contexto visível com a mesma página do PDF, incluindo passagens por bloco para materiais em colunas. O contexto é ampliado quando necessário para distinguir ocorrências repetidas. A comparação não troca letras nem números e preserva os delimitadores de estilo. Hífens Unicode em quebras de linha são reconhecidos; a evidência lexical considera variação de caixa e acentos ao decidir a fronteira, preservando os caracteres originais das palavras. Grafias já existentes na fonte permanecem preservadas.
- **URLs:** reconstrução do hífen literal comprovado no caminho; quebras físicas da fonte não autorizam inserir espaços dentro do endereço. Código protegido e URLs sem correspondência contextual não são reescritos.
- **Impressão:** todos os níveis `h1`–`h6` passam a manter o título junto ao conteúdo seguinte. Enumerações que continuam em células na página seguinte recebem a quebra interna adequada, preservando o marcador de origem.

As regras de fonte também se aplicam ao perfil de jurisprudência, sem impor a ele a hierarquia do perfil de curso. Fontes ausentes, ilegíveis ou criptografadas sem acesso não autorizam reconstruções presumidas. Nenhuma dependência, API externa ou exceção pelo nome da apostila foi acrescentada.

Arquivos principais: `course_editorial.py`, `markdown_utils.py`, `source_table_formatting.py` e `web/reading-export.html`.

## Fechamento do inventário

| Pendência | Resultado |
|---|---|
| 23 notas ATENÇÃO parcialmente enquadradas | Conteúdo da caixa original localizado em uma única nota da saída; regressão privada aprovada. |
| 10 caixas OBSERVAÇÃO como corpo comum | Reconhecidas e agrupadas; regressão privada aprovada. |
| 18 ocorrências de separadores | Formas defeituosas eliminadas e distinções da fonte preservadas; regressões aprovadas. |
| URL com `estadode` | Restaurado o trecho literal `estado-de`; não se presume funcionamento do site externo. |
| Enumeração `ii)`/`iii)` dentro da tabela | Itens separados, confirmados no DOM e na impressão do Edge. |
| Quatro títulos isolados | Nenhuma ocorrência restante nas impressões A4 examinadas. |

As antigas ocorrências impressas 5/22 e 5/84 aparecem agora nas páginas 23 e 86; as ocorrências 6/44 e 6/45, nas páginas 48 e 49. Em todos esses casos, o título está acompanhado do conteúdo na mesma página.

## Validações executadas

- **390 testes Python aprovados, sem testes ignorados**, com `NEXOJURIS_STRUCTURE_CORPUS` apontando para as entregas finais e `NEXOJURIS_EDITORIAL_AUDIT` para o inventário da revisão. A suíte inclui 16 testes novos de alinhamento/formatação da fonte e duas novas verificações do corpus privado.
- **11 testes JavaScript aprovados**, incluindo a aplicação da regra de impressão aos seis níveis de título.
- `ruff check .` e `git diff --check`: aprovados. Os avisos de conversão LF/CRLF não indicaram erro de whitespace.
- Markdown, JSON e payload dos HTMLs sincronizados; **209 títulos/âncoras**, **49 questões**, **49 respostas** e **zero itens de lista vazios**.
- Letras e dígitos visíveis preservados em relação à versão anterior, após remover apenas os marcadores técnicos de nota. Essa verificação é complementar à comparação com a fonte e não substitui a revisão de separadores.
- Revisões editoriais anteriores reaplicadas somente quando conteúdo e páginas permaneceram idênticos: 17 decisões na apostila 5 e 24 na 6. Sete antigos rótulos locais foram incorporados às notas completas comprovadas pela fonte; seu histórico foi preservado sem transferir decisões antigas ao conteúdo ampliado. Não restaram blocos classificados como ambíguos.
- Identificações privadas não foram reintroduzidas nos formatos finais. Os hashes das fontes foram reconferidos.

As execuções intermediárias que detectaram incompatibilidade com fontes ilegíveis/protegidas e limites de página foram corrigidas antes da suíte final. O log final é `docs/fidelity_audit_tmp/editorial-review/consolidated-python-final.log`; a mensagem de exceção simulada pelo teste de falha de índice faz parte desse teste e a suíte terminou com `OK`.

## Validação visual real no navegador

Foi utilizado o Edge conectado à sessão, por HTTP em `127.0.0.1`, conforme a capacidade atual documentada de teste local. O servidor ficou restrito ao endereço de loopback e ao diretório das entregas. Não foi necessário alterar permissões do navegador, contornar avisos ou publicar arquivos na internet.

Os dois HTMLs finais foram carregados e inspecionados, sem transbordamento horizontal no viewport de 1528 × 684. As impressões de teste foram geradas pelo próprio Edge em A4, com margens de 10 mm, fundos habilitados e cabeçalhos/rodapés do navegador desabilitados.

| Conferência | Apostila 5 | Apostila 6 |
|---|---:|---:|
| Páginas da nova impressão | 136 | 105 |
| Títulos localizados no PDF | 137/137 | 72/72 |
| Títulos isolados detectados | 0 | 0 |
| Páginas vazias | 0 | 0 |
| Páginas com texto fora dos limites | 0 | 0 |
| Elementos de texto HTML localizados na impressão | 1.259/1.259 | 842/842 |

As 241 páginas foram renderizadas e vistas em 13 folhas de visão geral. Foram examinadas ampliações das páginas 5/9, 5/19, 5/23, 5/86, 6/8, 6/19, 6/25, 6/26, 6/48, 6/49 e 6/67, incluindo caixas completas, nota com tabela e continuação entre páginas. As notas/tabelas também foram inspecionadas diretamente na tela do Edge. Os quatro títulos antes isolados, a enumeração e os enquadramentos examinados estão corrigidos.

Evidências: `docs/fidelity_audit_tmp/editorial-review/browser-final/`, com impressões de teste, imagens, `validation.json` e `dom-print-text-validation.json`. Essa pasta é temporária e ignorada pelo Git. Os PDFs de teste não substituem nem sobrescrevem os PDFs entregues pelo usuário.

## Entregas

Em `PDFs Convertidos/estrutura-revisada/`, foram atualizados os dois conjuntos `.md`, `.json`, `.html` e `.editorial-review.json`, além dos registros de revisão e impressão.

| HTML | SHA-256 |
|---|---|
| `direito_constitucional5.html` | `9c717e420e3c36a865d1efd1edd623a1f6ba8fe66a054cad863b02e4d68563f2` |
| `direito_constitucional6.html` | `39b07f9e8c6f30035981b01847b38a17b716596301c7c9f8b903d64b8d8265eb` |

Os avisos preservados sobre capas rasterizadas, divergências do sumário original e grafias já presentes na fonte não são pendências novas desta correção. A aprovação desta rodada cobre as pendências de formatação inventariadas e o cenário de impressão descrito; não é revisão jurídica nem certificação de qualquer PDF futuro. Mudanças de papel, escala, margens, fontes ou navegador podem alterar a paginação.

Não foram realizados commit, push, build de instalador ou release.
