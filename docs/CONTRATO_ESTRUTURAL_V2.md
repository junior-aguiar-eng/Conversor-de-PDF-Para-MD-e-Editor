# Contrato estrutural v2

O JSON exportado declara `schema_version: 2`. Os campos v1 permanecem disponíveis,
mas os IDs mudam de política. Consumidores que persistem IDs v1 precisam reindexar
o documento; não há equivalência posicional garantida. O Markdown incorporado ao
HTML continua idêntico ao `.md`, cujo SHA-256 acompanha o JSON.

## Perfil e proveniência

`profile_selection` informa o perfil solicitado, o efetivo e o método de seleção.
O padrão `auto` reconhece dois prefixos numéricos aninhados distintos em títulos
Markdown extraídos, excluindo sumários em tabela e números no corpo. Sem esse
sinal usa o perfil de boletim. Escolhas explícitas são respeitadas. A detecção
não garante classificação de qualquer gênero documental; materiais sem numeração
podem exigir seleção manual. O perfil `curso` admite doutrina, jurisprudência e
questões; não atribui todo o documento ao gênero jurisprudencial.

## Seções e intervalos

- `line` e `line_end`: linhas 1-based inclusivas do Markdown final, desde o título
  até antes do próximo título de nível igual ou superior. Incluem descendentes,
  metadados de proveniência e linhas em branco. Para recuperar a seção:
  `markdown.splitlines()[section['line'] - 1:section['line_end']]`.
- `page_start` e `page_end`: páginas físicas inclusivas ocupadas pelo título,
  conteúdo e descendentes. Uma página que contém apenas o marcador anterior ao
  próximo título não estende a seção encerrada. `null` significa origem desconhecida.
- `direct_line_end` e `direct_page_end`: fim do preâmbulo direto, antes do primeiro
  descendente. Sem descendentes coincidem com o intervalo completo. Não representam
  uma coleção de todos os trechos descontínuos de conteúdo direto.
- Continuidade textual entre páginas preserva cada `NEXOJURIS_PAGE` e registra
  `NEXOJURIS_CONTINUATION`. A indicação de página do trecho unido é inclusiva;
  não há coordenadas de caracteres no PDF para cada palavra.

O índice ignora código cercado por fences. A estrutura usa títulos ATX de topo;
títulos em HTML bruto ou dentro de listas/citações não viram seções por inferência.
O leitor verifica linha, nível e texto do token Markdown antes de atribuir o ID.
Títulos sem correspondência recebem âncora local e aviso; não deslocam os demais.

## Identificadores

`section-` seguido dos primeiros 20 dígitos hexadecimais do SHA-256 do caminho
textual ancestral, título e ocorrência entre caminhos homônimos. Reconversões
determinísticas preservam IDs; inserir outro título não renumera o restante.
Renomear/mover título ou ancestral, ou inserir um homônimo anterior no mesmo
caminho, pode mudar IDs. Não são identificadores imutáveis de identidade semântica.

## Questões e comentários

`questions` e `answers` são extensões de transcrição, sem atividades pedagógicas.
Cabeçalhos de coleções de questões e gabaritos delimitam seu contexto. Dentro dele,
prefixos numerados destacados com identificação da prova delimitam questões;
um número seguido de letra A–E delimita resposta expressa. Ambos viram filhos
de sua coleção, independentemente do nível atribuído pelo extrator.

Questões informam `number`, `section_id`, `stem`, `alternatives`, `answer`,
`ambiguities` e intervalos por linha/página. Alternativas só são separadas
automaticamente com sequência observada A, B, ... sem repetições ou saltos.
São preservadas quatro ou cinco alternativas quando observadas; nenhuma é criada.
Cada alternativa também informa intervalos por linha/página. A representação
textual projetada remove estilos e comentários de metadados; o Markdown continua
sendo a referência integral para recuperar a redação com estilos e proveniência.

`answer` só associa um cabeçalho expresso quando o número tem uma questão e uma
resposta únicas no documento. Coleções repetidas geram ambiguidade, sem escolher
uma associação. O comentário é preservado em Markdown e nunca é analisado para
deduzir resposta. Em particular, dizer que uma alternativa é CORRETA em um
comentário não a transforma no gabarito de uma questão que pede a INCORRETA.
Sequências ou associações inseguras constam em `ambiguities` e `editorial_issues`.

## Sumário, diagnósticos e fidelidade

`toc_entries` identifica referências pontilhadas em células de tabela. Títulos
reais únicos são associados pelo texto normalizado; os demais ficam `unresolved`.
`toc_discrepancies` preserva a página anunciada e informa a física real. A
transcrição permanece intacta. A navegação usa as seções reais, sem corrigir a fonte.

`checks` distingue `executed` de `not_run`; executar um detector não equivale a
aprovar o documento. Conferência visual e correção jurídica ficam `not_run` no
arquivo gerado. `editorial_stats` informa operações e contagens, e os avisos de
fragmentação lexical incluem página e linha para conferência, sem adivinhação.

`fidelity_score` é uma heurística lexical calculada antes da etapa editorial:
`0.65 * recall_de_tokens + 0.30 * similaridade_de_ordem + 0.05 * unicode`.
A ordem considera até 2000 tokens; o último termo vale 1 sem Unicode suspeito,
0 caso contrário. Valor `null` indica comparação indisponível ou insuficiente
(por exemplo, menos de 20 tokens na referência). Não é percentual comprovado de
fidelidade, nem mede estrutura documental, semântica jurídica ou semelhança visual.

## Páginas visuais e privacidade

O status legado de extração continua separado de `content_kind` e `visual_status`.
Uma página `fallback` pode preservar somente texto nativo residual e declarar
`image_text_unverified` / `omitted_privacy`; isso não significa capa convertida.
Falhas de carregamento/extração são identificadas; páginas vetoriais com desenhos
não são declaradas vazias só porque OCR e texto retornaram vazio. Uma página vazia
não contém texto, imagens utilizadas ou desenhos reconhecidos pelo extrator.

As marcas pessoais reconhecidas são redigidas no documento em memória antes de
extrair imagens ou executar OCR; o arquivo de origem nunca é salvo. Para páginas
com pouco texto nativo, imagem e identificação conhecida no documento, pixels/OCR
são omitidos porque a inspeção textual não prova a privacidade do raster. Isso é
uma limitação deliberada e explícita, não ausência de conteúdo. O OCR local já
existente continua disponível nos demais casos; nenhum serviço externo é usado.

Essa política não garante anonimização geral ou identificação de dados pessoais
existentes somente em imagens. Checkpoints anteriores à revisão são recalculados.
