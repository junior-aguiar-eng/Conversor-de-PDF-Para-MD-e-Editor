# Revisão do conversor e reconversão da apostila

## Base e método

Base da reprodução: `main`, commit `d7eee7b2c6e792790c139d683a829dc5f295f478`.
As correções foram integradas pela
[PR #4](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/pull/4),
merge `86bc46c`. A preparação da versão 1.6.2 está no commit
`d20bcbc2086dffe45a1262fd924d3cf5dd657f17` da `main`.
A [release publicada](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/tag/v1.6.2)
contém instalador, SHA-256 e relatório sanitizado dos gates.

PDF privado de 94 páginas, SHA-256
`3ae41fb4b6e47faa0be98ed858ac162805adf2b8e0d81f24329e64d976cef31d`.
A conversão v1.6.1 foi reproduzida antes das alterações e retornou os mesmos
143 títulos relatados. Entradas, intermediários e artefatos privados permanecem
fora do versionamento, nas pastas já ignoradas do projeto.

## Causas e correções

| Caso | Antes, reproduzido | Depois, verificado |
|---|---|---|
| Perfil | Padrão `jurisprudencia`; UI chamava-o de “Estrutura complexa” e chamava curso de “Estrutura simples” | Padrão automático, seleção efetiva `curso`; rótulos explícitos e escolha manual preservada |
| Hierarquia | Quase todos os títulos em H2 pela heurística do boletim | 126 seções; numeração define profundidade e subtítulos de letras pertencem ao instituto |
| Doutrina | Páginas 6–6 | Páginas 6–61, com descendentes |
| Habeas corpus | Páginas 6–6 | Páginas 6–12, com A–D como filhos |
| Subtítulo colado | “A. DIREITO À SAÚDE...” absorvido pelo título 1.2.6 | Separado mediante confirmação de linha própria em negrito na fonte; B–K retomam a mesma ramificação |
| Continuidade, p. 85 | “não-residentes.” era título | Parágrafo unido à página 84 com ambos os marcadores preservados |
| Comentário, p. 87 | “(A) CORRETA. Súmula...” era título, com `S úmula` | Comentário preservado; palavra recomposta por evidência textual da fonte |
| Dispositivo, p. 91 | `§ 2º Transitada...` era seção | Parágrafo destacado dentro do comentário, sem título artificial |
| Questões | Apenas a primeira era título, abrangendo as demais | 31 questões e 31 cabeçalhos de resposta, filhos de suas respectivas coleções |
| Alternativas | A da questão 2 no enunciado; questão 30 em um parágrafo | Enunciados e alternativas separados; questão 30 mantém somente A–D, conforme a fonte |
| Capas, p. 1–2 | Número da página, fallback e alerta lexical de 5% | Conteúdo visual identificado como não verificado, omissão por privacidade explícita e score indisponível |
| Sumário | Sem diagnóstico de erros da própria fonte | Três divergências registradas separadamente; transcrição preservada e navegação pela página física |
| Âncoras HTML | Associação por posição no DOM | Verificação de linha, nível e texto do token Markdown; títulos extras não deslocam IDs |
| Contrato e IDs | IDs sequenciais e intervalos apenas por página | Schema v2, limites por linha e IDs derivados do caminho textual; compatibilidade documentada |

As correções reutilizam a etapa editorial existente. A junção entre páginas agora
também ocorre depois de rebaixar falsos títulos. Palavras fragmentadas por estilos
só são recompostas quando aparecem inteiras no mapa textual da fonte, sem uma
forma separada observada. Cabeçalhos, alternativas e gabaritos dependem do contexto
da coleção; comentários não são usados para deduzir respostas.

As marcas pessoais reconhecidas são redigidas no PDF aberto em memória antes da
extração de pixels/OCR. A fonte permanece intacta. As capas não são exportadas em
imagem nem processadas por OCR porque a inspeção textual não verifica a privacidade
dos dados dentro do raster. Nenhuma API externa foi acrescentada.

Os campos e limitações estão no [contrato v2](CONTRATO_ESTRUTURAL_V2.md).

## Entregáveis locais

Na pasta `PDFs Convertidos/entrega/`:

- `direito_constitucional5.md`
- `direito_constitucional5.json`
- `direito_constitucional5.html`
- `verificacao.json`, com o resumo dos invariantes examinados

## Validação executada e consolidada na v1.6.2

- `uv sync --frozen`: ambiente Python 3.14 com o lockfile existente.
- `python -m unittest discover -s tests -v`: **338 testes aprovados**.
- Os seis arquivos `tests/*.test.js`: aprovados; incluem renderização offline,
  navegação, sanitização e regressões de títulos extras/índice divergente.
- `ruff check .`: aprovado. `scripts/verify_web_assets.py`: aprovado.
- `git diff --check`: aprovado.
- Reconversões integrais do PDF: Markdown idêntico em duas execuções da etapa
  de extração/estruturação, com a correção de hierarquia final.
- Hash do PDF confirmado; hash do Markdown corresponde ao JSON. Payload do HTML
  contém o Markdown externo idêntico e os mesmos metadados do JSON.
- Marcadores físicos 1–94 ordenados, sem lacunas; IDs únicos, pais de nível
  inferior e intervalos de linhas contidos nos pais.
- 31 questões e 31 gabaritos numerados 1–31, sem ambiguidades detectadas por esse
  parser neste documento; respostas exclusivamente dos cabeçalhos expressos.
- Nenhum identificador textual conhecido encontrado no Markdown, JSON ou HTML.
  A conversão final não exportou imagens.
- Inspeção visual dos recortes das páginas 1, 2, 6, 34, 73, 76, 83, 84, 85, 87,
  90 e 91 da fonte, com margens protegidas.
- HTML final executado no navegador: 126 títulos, 126 links de índice, todos os
  IDs encontrados; três tabelas; nenhuma âncora sem correspondência. Navegação
  por hash testada, questões 2 e 30 examinadas visualmente. Sem avisos/erros de
  console e sem requisições externas, apenas a leitura do próprio arquivo local.

`npm ci` e `npm run build:web`: aprovados após corrigir a configuração de build.
O CLI Tailwind 4.3.3 está fixado no lockfile junto do framework; a entrada CSS
carrega explicitamente a configuração existente e os arquivos da interface.
A compatibilidade dos estilos usados é examinada por uma regressão que compila
o CSS real. Os recursos gerados, inclusive DOMPurify já previsto no lockfile,
acompanham as alterações. O CI agora executa o build em Linux e Windows.

O override de `@parcel/watcher` 2.6.0 remove a cadeia vulnerável de dependências
de build da versão 2.5.1 fixada pelo CLI. `npm audit`: zero vulnerabilidades
reportadas na execução local de preparação.

## Distribuição Windows publicada

O instalador `NexoJuris-Conversor-Setup-v1.6.2.exe` foi compilado localmente a
partir da `main` e publicado com 139.070.844 bytes. Seu digest no GitHub coincide
com o SHA-256 local. O CI do commit `d20bcbc` aprovou os jobs Linux e Windows.

O gate do pacote aprovou runtime, importação WebView2, spawn, interface por
60 segundos, verificações NTFS, atualização, remoção e preservação dos dados
de teste. O gate Inno Setup aprovou instalação limpa, probe interno, registro do
desinstalador, atualização, remoção e dados preservados. Esse segundo gate foi
local; não é uma etapa do CI.

A preparação também corrigiu uma fixture de OCR que usava pixels não
inicializados e podia ser detectada como vazia antes do OCR simulado. As imagens
de teste passaram a ser inicializadas e seu cache foi isolado; o comportamento
de OCR em produção não foi alterado por essa correção de teste.

O aplicativo de uso diário não foi instalado nem ativado por esses ensaios.
O instalador permanece sem assinatura Authenticode. Links, hash, comandos e
limites estão em [Distribuição Windows](DISTRIBUICAO_WINDOWS.md).

## Limites preservados

- Capas permanecem sem transcrição do texto em imagem; a omissão está explícita.
- “manda- do”, p. 76, permanece com alerta lexical rastreável. A inspeção visual
  confirma o espaçamento na própria fonte; não foi corrigido por inferência.
- Nacionalidade 72→55, Jurisprudência 80→62 e Gabarito comentado 107→84 são
  discrepâncias do sumário original, não falhas atribuídas à extração.
- A inspeção visual foi amostral, não uma certificação das 94 páginas. O score
  lexical não certifica estrutura, redação jurídica ou fidelidade visual.
- O parser é conservador e não cobre todo formato possível de prova ou sumário;
  coleções repetidas e sequências inseguras geram ambiguidades. IDs mudam quando
  títulos/ancestrais são renomeados, movidos ou recebem homônimos anteriores.
- Consumidores de IDs v1 precisam reindexar o schema v2. Nenhum consumidor,
  histórico de estudo, grafo ou serviço de busca foi implementado.
