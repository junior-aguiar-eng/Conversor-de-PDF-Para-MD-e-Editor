# Correção geral da estrutura documental

Este relatório registra a primeira etapa. A revisão posterior dos 53 casos das
apostilas 5 e 6 e os artefatos atualizados estão no
[relatório de revisão editorial](RELATORIO_REVISAO_EDITORIAL_5_6.md).

Base examinada: `main`, `b41a7679fdcaa8a2efbce0bdab9d4b614095ca90`, checkout
`Conversor PDF 2`, inicialmente limpo. Implementação local em 15/10/2026.
Sem alteração de dependências, licenciamento, instalação ou serviços externos.

## Diagnóstico e implementação

O projeto já possuía preparação editorial, índice comum e leitor com âncoras
verificadas. A evolução aproveita essas camadas, sem reescrever a extração.

| Causa demonstrada | Correção |
|---|---|
| Prioridade de `I` romano mesmo entre letras consecutivas | Considera vizinhos anteriores e seguintes e mantém a pilha de tipos existente |
| Classificação isolada dos marcadores, desconsiderando pares em lista/corpo | Reconcilia enumerações locais e rebaixa títulos assimétricos |
| Apenas maiúsculas com ponto eram reconhecidas | Aceita também maiúsculas com parêntese, preservando o marcador original |
| Título absorvia explicação; subtítulo sem número desaparecia | Usa linhas/spans da fonte protegida, tamanho, fonte e negrito recorrente para separar rótulos e reconhecer candidatos |
| Parser aceitava somente `(A)`–`(E)` | Aceita marcadores variados, espaços ausentes e quantidade observada; sequência inconsistente permanece intacta e diagnosticada |
| Fim inclusivo calculado sobre espaços antes da próxima alternativa | Remove a região separadora antes de calcular o último caractere e a linha final |
| Sumário com grupos de pontos separados por espaços não correspondia | Normalização exclusiva para comparação e estado explícito de ambiguidade |
| Ausência de índice dos blocos e de resultado dos invariantes | Acrescenta blocos rastreáveis e validação com estados distintos de execução |

`document_blocks.py` projeta os blocos a partir do Markdown final e dos índices
já calculados. `document_exports.py` incorpora o mesmo conteúdo e as mesmas
decisões no JSON e HTML. A fonte física, a estrutura editorial e os diagnósticos
têm campos distintos. O [contrato da revisão 3](CONTRATO_ESTRUTURAL_REVISAO_3.md)
define coordenadas, conteúdo direto, descendentes, IDs locais e identidade de
documento/versão. Os campos antigos do schema v2 permanecem disponíveis.

## Regressões das referências

| Caso | Resultado da reconversão e verificação |
|---|---|
| Continuação 84–85 da apostila 5 | `não-residentes.` permanece no comentário, sem seção artificial |
| Comentário da página 87 | Palavra `Súmula` preservada; resposta vem do cabeçalho expresso, sem inferência do comentário |
| Dispositivo da página 91 | `§ 2º` permanece no corpo |
| Doutrina, remédios e habeas corpus | Intervalos físicos 6–61, 6–28 e 6–12, respectivamente; árvore com pais existentes e níveis coerentes |
| H, I, J, K | Mesmo pai e nível 5 |
| Questões 2 e 30 da apostila 5 | Enunciados separados e A–D observadas em ambas; não se inventa E |
| Sumário da apostila 5 | Preservadas divergências 72→55, 80→62 e 107→84 |
| `e)` da questão 3 da apostila 6 | Alternativa E própria, iniciando na página física 64 |
| ADO/MI | Ambos como itens de lista; nenhum deles é promovido isoladamente |
| Requisitos a–d | `d)` rebaixado ao mesmo tratamento dos demais |
| Vício formal/material | Rótulos separados da explicação, como seções irmãs |
| SÚMULAS DO STF/JULGADOS | Duas seções irmãs na página 46 |
| Pontilhados e intervalos inclusivos | Regressões sintéticas e inspeção dos intervalos das 49 questões aprovadas |

As regras de produção não contêm as frases, páginas ou nomes desses casos.
Os exemplos específicos ficam apenas nos testes opcionais do acervo privado.

## Corpus e limites da validação

Todos os cinco PDFs foram reconvertidos integralmente, totalizando 324 páginas.
Os dois últimos documentos foram reservados e processados após o desenvolvimento
das regras. Não houve ajuste de regras a partir desses dois resultados.

| Documento | Uso | Páginas | Seções | Blocos | Questões/respostas |
|---|---|---:|---:|---:|---:|
| direito_constitucional5 | Desenvolvimento/regressão | 94 | 133 | 1212 | 31/31 |
| direito_constitucional6 | Desenvolvimento/regressão | 79 | 72 | 803 | 18/18 |
| manual_administrativo7 | Desenvolvimento/compatibilidade | 78 | 99 | 945 | 0/0 |
| precedentes_obrigatorios | Reservado | 29 | 63 | 292 | 0/0 |
| responsabilidade_civilpdf | Reservado | 44 | 70 | 635 | 0/0 |

Não houve página com status `failed` nem falha dos invariantes verificados.
As 49 questões das referências não têm ambiguidades de alternativas/respostas
apontadas pelo parser. Isso não prova que todos os componentes de todo documento
foram encontrados. Os três documentos adicionais não validam reconhecimento de
questões, pois o parser não identificou coleções de questões neles.

O resultado estrutural global continua **inconclusivo**: os documentos registram,
respectivamente, 25, 28, 13, 24 e 29 blocos destacados sem confirmação de função
estrutural. São pontos para revisão, não erros editoriais automaticamente
comprovados. Sumários: oito entradas reconhecidas na referência 5, com cinco
correspondências e três divergências; seis correspondências na referência 6.
Nos outros três não houve comparação de sumário executada.

Foram inspecionadas visualmente as páginas protegidas 6, 55, 62, 73, 84, 85,
87 e 91 da referência 5 e 13, 14, 17, 18, 46 e 64 da referência 6. Isso cobre
os exemplos representativos; não constitui revisão visual integral do corpus.

O HTML foi executado em JSDOM: 437 títulos e 437 links com alvos existentes,
sem erros de script, payload igual ao JSON e Markdown externo idêntico. A
inspeção visual em navegador **não foi concluída**: Chrome DevTools encontrou
conflito de perfil; o navegador integrado bloqueou `file:` por política de
segurança. Não foi adotado contorno dessa restrição. JSDOM não valida aparência,
rolagem real ou fidelidade visual de tabelas e imagens.

## Verificações executadas

- Suíte Python completa, com o acervo privado configurado: **352 testes aprovados**.
- Os seis arquivos `tests/*.test.js`: **11 testes aprovados**.
- `ruff check .`, `scripts/verify_web_assets.py` e `git diff --check`: aprovados.
- `node scripts/verify-structured-exports.js "PDFs Convertidos/estrutura-final"`:
  cinco conjuntos de formatos aprovados, com 133, 72, 99, 63 e 70 âncoras.
- Hashes das fontes preservados; hash do Markdown corresponde ao JSON;
  payload HTML corresponde integralmente ao JSON e Markdown externos.
- Conteúdo de cada bloco corresponde literalmente ao intervalo no Markdown.
- Nova aplicação da proteção textual aos três formatos não exigiu substituições;
  testes existentes de privacidade e sanitização passaram.

Para reproduzir as regressões privadas em PowerShell:

```powershell
$env:NEXOJURIS_STRUCTURE_CORPUS = (Resolve-Path 'PDFs Convertidos/estrutura-final').Path
.venv\Scripts\python.exe -m unittest discover -s tests -p test_private_structure_corpus.py
```

O corpus privado não é versionado. Os testes sintéticos independem dele. Os
formatos de entrega e `verificacao.json` ficam em `PDFs Convertidos/estrutura-final/`,
diretório já ignorado pelo Git. Logs, extrações intermediárias e PNGs protegidos
ficam em `docs/fidelity_audit_tmp/general/` e arquivos associados.

## Entregáveis

Cada documento possui `.md`, `.json` e `.html` na pasta de entrega:

- `direito_constitucional5`
- `direito_constitucional6`
- `manual_administrativo7`
- `precedentes_obrigatorios`
- `responsabilidade_civilpdf`

## Limitações restantes

Classificação ainda depende da qualidade da extração e da regularidade editorial.
Negrito curto sem evidência suficiente é preservado com diagnóstico. Listas
internas ambíguas ou sequências de alternativas corrompidas não são completadas.
Não há alinhamento palavra por palavra com coordenadas do PDF, nem promessa de
generalização a todo o acervo. Notas/citações/tabelas recebem os tipos que a
extração e a sintaxe permitem distinguir; não há reconstrução garantida de toda
caixa visual, nota de rodapé ou subenumeração complexa.

Continuam os avisos lexicais que já exigem inspeção da fonte, sem correção
jurídica presumida. Capas/raster podem permanecer omitidos por privacidade;
ausência de erro de extração não significa ausência de conteúdo visual omitido.
Nenhuma verificação aqui certifica fidelidade jurídica, anonimização geral ou
equivalência visual. A revisão visual do HTML permanece pendente.
