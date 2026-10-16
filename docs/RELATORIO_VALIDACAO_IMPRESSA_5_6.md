# Validação visual das impressões das apostilas 5 e 6

> Fechamento atual: **correção consolidada e validação no Edge/A4 concluídas em 16/10/2026**. Foram examinadas as 241 páginas das novas impressões automáticas, com zero títulos isolados. Veja [o relatório final](RELATORIO_CORRECAO_CONSOLIDADA_5_6.md). As conclusões abaixo permanecem como histórico das impressões anteriores.

> Atualização de 16/10/2026: a [revisão consolidada](RELATORIO_REVISAO_CONSOLIDADA_5_6.md) substitui a conclusão anterior de que restava apenas uma ressalva leve. Foram identificados problemas recorrentes de agrupamento de caixas, títulos isolados e separadores. A aprovação visual permanece pendente. As rodadas abaixo são mantidas como histórico.

Data: 15/10/2026. Resultado: **inspeção concluída com defeitos; aprovação visual pendente**.

Última rodada: **16/10/2026**. As duas correções anteriores foram confirmadas
na nova impressão. Foram identificados defeitos adicionais em tabelas; a
aprovação integral permanece pendente. Esses defeitos foram depois corrigidos
por uma regra geral e os formatos foram regenerados. A nova impressão confirmou
as correções, com uma ressalva tipográfica leve na continuação de uma enumeração.
As seções finais documentam a implementação e a última conferência.

## Material e método

Foram examinados os dois PDFs fornecidos pelo usuário, produzidos pela impressão
do HTML no Edge. A apostila 5 contém 135 páginas impressas; a apostila 6, 102.
Esses números refletem a repaginação do HTML e não precisam coincidir com as
94 e 79 páginas físicas das fontes, respectivamente.

Todas as 237 páginas foram renderizadas e examinadas em 13 folhas de visão
geral. Páginas selecionadas foram examinadas em resolução maior, incluindo
sumários, títulos, questões, comentários e finais dos documentos. Trata-se de
visão geral integral com inspeção detalhada seletiva, não de conferência palavra
por palavra de todas as páginas.

A extração geométrica não encontrou páginas sem texto nem caixas de texto
fora dos limites físicos das páginas, com tolerância de 1 ponto. Essa verificação
não demonstra, sozinha, ausência de sobreposição ou de cortes internos em tabelas.
Nas páginas detalhadas examinadas, a tipografia está legível e não foram
observadas sobreposições ou tabelas excedendo a largura útil.

As evidências locais, renderizações, mapas de páginas e verificações geométricas
estão em `docs/fidelity_audit_tmp/editorial-review/printed/`. Os PDFs recebidos
foram apenas lidos; não foram alterados.

| Entrada | Páginas | SHA-256 |
| --- | ---: | --- |
| Apostila 5 — leitura editorial | 135 | `031b9fdef555339bfa46c9f7a8db71909e1ad371a19d06414368c21973426328` |
| Apostila 6 — leitura editorial | 102 | `3d500aadc8e2059df0449c2a14d505143b0bb77c1ab3dd7ef6e92bc7a51f41d4` |

## Achados confirmados

### 1. Apostila 5: agrupamento incompleto da caixa de atenção

Na página impressa 19, a caixa inclui somente `ATENÇÃO!`; os parágrafos das
Súmulas 624 e 623 aparecem fora dela. Na página física 16 da fonte, os três
elementos pertencem à mesma caixa. O conteúdo textual está presente, mas sua
associação visual foi perdida. Confirma o achado da captura de tela anterior.

Estado: **defeito ainda presente, pendente de correção**. A correção deve usar
evidência de agrupamento da fonte, sem estender indiscriminadamente toda caixa
de atenção aos parágrafos seguintes.

### 2. Apostila 6: quatro marcadores vazios na questão 10

Na página impressa 84, aparecem quatro pontos de lista vazios entre as
alternativas A–E. A página física 66 da fonte foi renderizada e comparada:
contém apenas as alternativas, sem esses marcadores.

O problema também permanece no Markdown atual de
`PDFs Convertidos/estrutura-revisada/direito_constitucional6.md`, a partir da
linha 1373: quatro linhas isoladas com `-` são interpretadas como itens vazios.
Assim, este defeito não pode ser atribuído somente à impressão de versão antiga.

Estado: **defeito confirmado, pendente de correção**. Uma correção deve distinguir
esses separadores vazios de sinais ou itens com conteúdo legítimo.

### 3. Apostila 6: impressão incompatível com a revisão atual

Na página impressa 12, o título `A. VÍCIO FORMAL` incorpora toda a explicação
seguinte na tipografia azul dos títulos. Na página 14, `B) VÍCIO MATERIAL`
permanece em parágrafo de corpo, com tratamento assimétrico.

O Markdown revisado atual, nas linhas 230 e 262, contém dois títulos irmãos
separados das respectivas explicações. Os números de linha dos diagnósticos na
impressão também divergem dos atuais. Essas evidências demonstram que o PDF
recebido não representa a revisão atual desse trecho. Não foi identificado qual
arquivo HTML foi usado pelo usuário para imprimir.

Estado: **este PDF não certifica visualmente a versão atual da apostila 6**.
Os problemas de hierarquia nele observados não foram classificados como
regressões da revisão atual, pois esta já apresenta outra estrutura.

## Correções anteriores observáveis na apostila 5

Na impressão recebida, o subtítulo `A.1. Adjudicação` aparece como título na
página 47. Foram também examinados os subtópicos recuperados, a região dos
itens H/I, questões e comentários selecionados. A questão 2 apresenta suas
alternativas separadas na página 101, e a questão 30 apresenta A–D na página
118, seguida pela questão 31, sem criação de alternativa E. Os comentários
selecionados permanecem no corpo, incluindo `não-residentes` na página 120,
a referência à Súmula Vinculante 12 na página 124 e o trecho `§ 2º Transitada`
na página 130. O comentário 31 está presente na última página, 135.

Esses casos fornecem evidência visual das correções específicas; não demonstram
fidelidade textual integral nem revisão da correção jurídica da fonte.

## Conclusão e trabalho restante

A inspeção estática das impressões foi concluída e identificou dois defeitos
reais de apresentação/conversão. A aprovação visual permanece pendente até sua
correção e conferência de novas renderizações correspondentes aos arquivos
revisados. A impressão da apostila 6 precisa corresponder à versão atual.

Os HTMLs revisados estão em `PDFs Convertidos/estrutura-revisada/`. Nenhum HTML,
Markdown, JSON ou código do conversor foi alterado nesta etapa de inspeção; foram
produzidos evidências e este relatório. Não foram repetidos testes de código,
pois esta etapa não modificou a implementação.

Os PDFs permitem avaliar apresentação impressa. Não comprovam cliques nas
âncoras, responsividade, rolagem ou outros comportamentos interativos. Esses
aspectos continuam sem inspeção visual em navegador. Os campos automáticos de
fidelidade visual não foram promovidos a aprovação.

## Correção e regeneração para nova impressão

Após a inspeção acima, os dois defeitos foram corrigidos no conversor e os
artefatos de `PDFs Convertidos/estrutura-revisada/` foram regenerados em
15/10/2026. Os achados anteriores descrevem os PDFs recebidos, não o estado
dos HTMLs agora entregues.

- A caixa da página física 16 da apostila 5 é reconhecida pelas quatro bordas
  vetoriais da fonte. O texto protegido contido nela precisa corresponder
  integralmente aos parágrafos do Markdown, na mesma página. A nota regenerada
  inclui o rótulo e as duas súmulas; o aviso seguinte permanece fora dela.
- A separação de alternativas retira o prefixo de lista associado ao marcador
  na mesma linha, somente em uma sequência A/B/... confirmada. Foram removidos
  os quatro prefixos da questão 10 da apostila 6 e um prefixo análogo na apostila
  5. Nenhum item vazio aparece no DOM dos dois HTMLs regenerados.
- A apostila 6 foi regenerada da revisão atual: os títulos de vício formal e
  material permanecem irmãos e separados das explicações. Não foi reutilizado
  o HTML antigo que originou a impressão recebida.

As decisões editoriais foram reaplicadas somente após comprovar identidade de
conteúdo e intervalo de páginas de cada bloco: 20 na apostila 5 e 28 na 6. Os
hashes e IDs foram atualizados; nenhum bloco ambíguo ficou sem decisão. Foram
mantidos os 137/72 títulos, as 31/18 questões e as 31/18 respostas. A comparação
dos Markdown anterior e novo limita as mudanças ao agrupamento da nota, aos
prefixos vazios e aos espaços finais afetados pela separação.

Verificações executadas após as correções:

- `python -m unittest discover -s tests`, com o acervo regenerado configurado:
  **363 testes aprovados**, incluindo as regressões privadas.
- `node --test` nos arquivos `tests/*.test.js`: **11 testes aprovados**.
- `ruff check .` e `git diff --check`: aprovados.
- Verificador dos formatos e âncoras: **209 âncoras válidas**, payloads idênticos
  aos JSON/Markdown e nenhum erro de execução no JSDOM.
- Verificação dirigida do DOM, repetida no destino final: uma caixa com três
  parágrafos para a nota corrigida; alternativas A–E da questão 10 separadas;
  zero itens de lista vazios nos dois HTMLs.
- Proteção textual aplicada a Markdown, JSON, HTML e registros de revisão:
  nenhuma substituição adicional necessária nos artefatos preparados.

Foram preservadas cópias anteriores no diretório local de evidências. Os hashes
das novas entregas e o estado `ready_for_reprint` estão em
`PDFs Convertidos/estrutura-revisada/impressao-revisada.json`; as instruções de
impressão estão em `IMPRESSAO-REVISADA.txt`, na mesma pasta. Não foram feitos
commit, push ou publicação.

**Estado atual: pronto para nova impressão.** Os testes e o DOM demonstram as
correções estruturais; a aparência da nova paginação ainda requer os novos PDFs
impressos. A aprovação visual final continua pendente dessa conferência.

## Nova impressão recebida e revisada em 16/10/2026

Os arquivos com os mesmos nomes da primeira rodada foram substituídos pelo
usuário. Ambos agora contêm PDFs válidos: 135 páginas na apostila 5 e 102 na 6.
Os PDFs foram lidos sem alteração; os hashes foram novamente conferidos no fim
da inspeção, para excluir mudança de arquivo durante a revisão.

| Nova impressão | SHA-256 |
| --- | --- |
| Apostila 5 | `a29fc992f27843f11a92f5c6efaa879bd275c0aa65b83aa4b4018a75d60d12bc` |
| Apostila 6 | `05b2eb93174654423c6414ee1c161bc9a457bafd6b58bd33110818a10c24011e` |

### Método e resultados gerais

As 237 páginas foram renderizadas e examinadas em 13 folhas de visão geral.
Foram examinadas em resolução maior as páginas impressas 19, 47, 84, 101 e
135 da apostila 5 e 8, 12, 14, 18, 53, 84, 85 e 102 da apostila 6. A visão
geral não equivale à leitura detalhada de todas as palavras. As evidências desta
rodada ficam em `docs/fidelity_audit_tmp/editorial-review/printed-2026-10-16/`.

Não foram encontradas páginas sem texto nem caixas de texto fora dos limites
físicos das páginas, com tolerância de 1 ponto. Nas páginas detalhadas, não
foram observados cortes de tabela ou sobreposições. Essa observação não elimina
os defeitos internos de conteúdo e agrupamento descritos abaixo.

Uma comparação complementar encontrou no texto extraído dos PDFs os textos de
todos os 1261 elementos selecionados do DOM da apostila 5 e dos 848 da apostila
6: parágrafos, títulos, células e itens de lista. A comparação normaliza letras e
números, descartando espaços e pontuação; portanto, **não detecta as palavras
coladas nem certifica a ordem integral do documento**. Não houve correspondência
contínua única do corpo completo. O resultado demonstra presença dos elementos
esperados, não fidelidade à fonte. Os hashes dos HTMLs atuais continuam iguais
aos registrados na entrega de 15/10.

### Correções anteriores: confirmadas

- **Apostila 5, página impressa 19:** a mesma barra lateral engloba o rótulo
  `ATENÇÃO!` e as Súmulas 624 e 623. O aviso sobre vetos presidenciais aparece
  em outra caixa. O defeito anterior de agrupamento está resolvido.
- **Apostila 6, páginas impressas 84–85:** as alternativas A–E da questão 10
  aparecem separadas, sem os quatro marcadores vazios. A passagem de página
  entre B e C não elimina alternativas.
- **Apostila 6, páginas impressas 12 e 14:** `A. VÍCIO FORMAL` e
  `B) VÍCIO MATERIAL` aparecem como títulos com tratamento equivalente e
  explicações em parágrafos separados. A divergência de versão observada na
  impressão anterior não se repete nesses trechos.

Também foram conferidos o subtítulo recuperado `A.1. Adjudicação` na página 47
da apostila 5, as alternativas A–D da questão 2 na página 101, o comentário 31
na última página da apostila 5 e o comentário 18 na última página da 6. Os
itens ADO/MI aparecem com tratamento simétrico na página impressa 18 da 6.

### Defeitos adicionais confirmados nas tabelas

**Apostila 5 — página impressa 84, tabela de brasileiros natos/naturalizados.**
Na linha do Conselho da República, o resultado apresenta
`brasileirosnatosintegram`; na redação da perda de nacionalidade também aparece
`dadapela`. A página física 60 da fonte foi renderizada e comparada: as palavras
estão separadas. São falhas de espaçamento da conversão, não da impressão.
O Markdown atual já contém as junções, inclusive junto dos delimitadores de
negrito. A tabela cabe na página, mas o conteúdo não pode ser aprovado como fiel.

**Apostila 6 — página impressa 8, quadro dos sistemas austríaco/norte-americano.**
O resultado divide `Anulabilidade` entre um cabeçalho `daAnulabi-` e a célula
seguinte `lidade.Nesse`; também apresenta `daNulidade.O` e marcadores de ênfase
literais em `_ex nunc_`/`_ex tunc_`. Na fonte, páginas físicas 9–10, a palavra
quebrada entre linhas pertence à mesma célula de texto, e os termos latinos
estão formatados sem sublinhados literais. A continuação da tabela é exportada
como outra tabela e seu primeiro trecho é tratado como cabeçalho, tornando
centrada uma parte que deveria continuar a descrição da coluna.

Esses defeitos já estão representados no Markdown/HTML e foram identificados
pela inspeção mais detalhada das tabelas nesta rodada. Não indicam retorno dos
dois defeitos corrigidos. As correções seguintes devem preservar células e
continuações, restaurar espaços atestados na fonte e tratar a ênfase inline, sem
substituições globais de palavras presumidas.

### Estado ao encerrar esta revisão

**Os dois defeitos anteriores estão visualmente resolvidos. A aprovação visual
integral continua pendente devido aos defeitos adicionais das tabelas.**

O manifesto `impressao-revisada.json` foi atualizado com os hashes das novas
impressões, o resultado dos casos corrigidos e os achados remanescentes. Os
PDFs, HTMLs, Markdown, JSON estruturais e código do conversor não foram
alterados nesta rodada; somente relatórios/evidências e o manifesto de revisão.
Não foram repetidos testes de código nesta inspeção documental.

Esta revisão avalia a apresentação impressa e a fidelidade editorial observável.
Não constitui revisão da correção jurídica do material nem inspeção interativa
do navegador. Os estados automáticos de fidelidade dos JSON não foram promovidos
a aprovação.

## Correção geral posterior dos defeitos das tabelas

Os defeitos encontrados na rodada de 16/10 foram corrigidos na etapa comum de
formatação de tabelas, sem regras vinculadas a frases ou nomes das apostilas.
Os HTMLs de `estrutura-revisada` foram regenerados. O relatório
[RELATORIO_FORMATACAO_GERAL_TABELAS.md](RELATORIO_FORMATACAO_GERAL_TABELAS.md)
descreve os critérios, os limites e a validação: 372 testes Python, 11 JavaScript,
209 âncoras válidas e verificações dirigidas das células afetadas.

Estado atual: **correções implementadas; pronto para nova impressão**.
O resultado visual dos PDFs recebidos permanece registrado como histórico.
A paginação dos HTMLs posteriores à correção ainda requer nova conferência.

## Conferência posterior à correção geral das tabelas — 16/10/2026

Foram recebidos novos PDFs nos mesmos caminhos. Permanecem com 135 páginas na
apostila 5 e 102 na 6. A rodada foi preservada separadamente em
`docs/fidelity_audit_tmp/editorial-review/printed-after-tables/`, com os hashes:

| Impressão conferida | SHA-256 |
| --- | --- |
| Apostila 5 | `eebb304e0ea302440b5278addc1ce70c7b654064f55e3853565b84e394b2f952` |
| Apostila 6 | `34703eb6b1a06d158bac5a2798f85c11b72ebaf742dfdc6f4a244e9d4afd038e` |

### Cobertura

As 237 páginas foram renderizadas e examinadas em 13 folhas de visão geral.
Foram inspecionadas em maior resolução as páginas impressas 19, 45, 84 e 135
da apostila 5, e 8, 52, 84 e 102 da apostila 6. Isso inclui as quatro tabelas
reconstruídas, os dois defeitos anteriores de nota/alternativas e os finais dos
documentos. A visão geral não constitui leitura palavra por palavra.

Não foram encontradas páginas sem texto nem caixas de texto fora dos limites
físicos, com tolerância de 1 ponto. Nas páginas detalhadas não foram observados
cortes de tabela, sobreposições ou texto ilegível. A paginação mudou em alguns
trechos: por exemplo, o comentário 31 da apostila 5 começa na página 134 e
continua na 135; o comentário 18 da 6 começa na 101 e termina na 102.

A comparação complementar encontrou no texto extraído dos PDFs todos os textos
dos 1261 elementos selecionados do DOM da apostila 5 e dos 842 da 6, sem elementos
ausentes nesse teste. Como a comparação descarta espaços e pontuação, ela não
prova fidelidade gráfica, ordem integral, nem correção jurídica. A observação
visual é a evidência das correções de espaçamento e estilo.

### Correções confirmadas nesta impressão

- **Apostila 5, página 84:** `Seis brasileiros natos integram` está separado
  corretamente e somente `natos` recebe o destaque esperado. As ocorrências de
  `dada pela` também estão separadas. As enumerações da célula de cargos mantêm
  quebras de linha. O quadro cabe na página, sem cabeçalho artificial.
- **Apostila 6, página 8:** `Anulabilidade` está inteira dentro da mesma célula;
  o quadro contém os dois sistemas na mesma tabela, sem transformar a continuação
  em cabeçalho. `ex nunc` e `ex tunc` estão em itálico, sem underscores literais.
- **Demais tabelas reconstruídas:** a tabela da apostila 5 na página 45 apresenta
  as linhas de conteúdo com seus rótulos; a tabela de ações na página 52 da
  apostila 6 mantém um cabeçalho real e cinco linhas de dados legíveis.
- **Correções anteriores preservadas:** a caixa da página 19 da apostila 5 inclui
  o rótulo e as duas súmulas, separada do aviso seguinte. Na página 84 da apostila
  6, as alternativas A–E da questão 10 aparecem sem marcadores vazios.

### Ressalva tipográfica remanescente

No quadro da apostila 6, página 8, o início de `iii)` aparece na mesma linha do
final de `ii)`. O texto está completo, separado por espaço e pontuação, e o
marcador permanece legível. É uma ressalva de organização da enumeração, não
perda de conteúdo ou retorno da fragmentação das células.

Para reproduzir a apresentação da lista da fonte, a união de células entre
páginas deve usar quebra de linha quando o fragmento continuado começa por
marcador de enumeração. Esse critério deve ser geral, sem testar o texto ou o
número de um item específico. Não foi feita alteração de código nesta rodada.

### Resultado desta rodada

**As correções de espaços, palavras fragmentadas, estilos, cabeçalhos e
agrupamento das tabelas foram confirmadas visualmente. Revisão impressa
concluída com uma ressalva tipográfica leve na apostila 6.**

Não foi declarada ausência de defeitos em toda palavra dos documentos nem
aprovação visual sem ressalvas. O manifesto de impressão foi atualizado com
esse resultado e os novos hashes. HTMLs, Markdown, JSON estruturais, código,
PDFs-fonte e PDFs recebidos permaneceram inalterados nesta rodada. Os estados
automáticos de fidelidade e a inspeção interativa do navegador não foram
promovidos a aprovação.
