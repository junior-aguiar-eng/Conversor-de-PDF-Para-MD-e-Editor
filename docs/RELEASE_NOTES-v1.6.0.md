# NexoJuris Conversor v1.6.0

Cada conversão concluída agora gera Markdown, JSON de estrutura e HTML para leitura
offline na mesma pasta. O perfil de material de curso recebe correções editoriais
para preservar hierarquia, separação de assuntos e proveniência de páginas.

## Conversão e fidelidade editorial

- Reparo conservador de palavras fragmentadas, hifenização, espaçamento e
  continuações entre páginas, usando evidência do texto nativo do PDF.
- Correção de títulos falsos, organização de notas locais e separação de
  coleções jurisprudenciais, súmulas e temas.
- Preservação de tabelas, referências às páginas físicas, sublinhados e destaques.
- Remoção de recortes residuais de margem somente com evidência de geometria
  e pixels; tamanho ou repetição de uma imagem não bastam para removê-la.
- Extração de páginas em sequência dentro de cada processo. O paralelismo
  por threads da versão 1.5.0 podia alterar a estrutura de leitura; PyMuPDF
  não suporta esse uso. O paralelismo entre documentos em processos separados
  permanece disponível. `page_workers` continua aceito por compatibilidade,
  sem habilitar extração simultânea em threads.

## JSON de estrutura

O JSON acompanha o Markdown com identificadores, relações pai-filho, níveis,
títulos, contexto ancestral, linhas e intervalos de páginas. Inclui cobertura
da extração, alertas editoriais e hashes SHA-256 do PDF e do Markdown final.
É um mapa para indexação e processamento por assunto; não duplica o texto.
Para enviar o conteúdo a uma IA em uma conversa, o Markdown continua suficiente.

## HTML para leitura offline

Índice lateral navegável, layout adaptável, tabelas, notas, destaques e impressão.
O conteúdo, Marked, DOMPurify e suas licenças ficam incorporados ao HTML, que abre
diretamente do disco sem depender do JSON ou de um servidor. Quando houver imagens,
mantenha a pasta `images/` ao lado dos arquivos. Conteúdo ativo, imagens remotas
e links locais externos extraídos do documento são bloqueados pelo leitor.

## Integridade da entrega

O conversor reserva os nomes dos três formatos e acrescenta um sufixo quando
qualquer destino já existe. Prepara o conjunto antes de publicá-lo e grava
o Markdown por último. Falhas controladas removem os arquivos da tentativa;
uma queda abrupta pode deixar companheiros órfãos, que não são sobrescritos.
A interface informa os caminhos do JSON e do HTML no registro da conversão.

## Validação e limites

313 testes Python e 11 testes JavaScript aprovados, além de Ruff, validação
dos recursos web e conferência de consistência das versões de release.

As correções foram testadas em uma apostila digital de 78 páginas: o fluxo
da API produziu novamente o mesmo texto editorial aprovado, com 99 seções,
97 relações pai-filho, seis tabelas e cinco partes opcionais em Markdown.
O HTML foi conferido no navegador, incluindo navegação e larguras distintas.
Esses resultados são evidência da amostra, não garantia de extração perfeita
para qualquer PDF ou certificação da correção jurídica do conteúdo.

HTML e JSON são fotografias da conversão. Editar o Markdown posteriormente
não atualiza automaticamente seus companheiros. O hash permite detectar
essa divergência. A geração de HTML sanitizado não torna confiáveis as
instruções contidas no documento de origem.

A release acompanha os resultados dos testes e verificações de empacotamento.
O instalador não tem assinatura Authenticode. O build local usa a alternativa
Python empacotada pelo PyInstaller: a etapa opcional Cython não foi aplicada
devido a uma falha C1083 de geração de objetos em caminho longo do Windows.
As funções e os recursos distribuídos foram conferidos no pacote.
