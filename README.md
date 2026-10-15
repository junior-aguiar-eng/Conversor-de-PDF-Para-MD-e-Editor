# NexoJuris - Conversor

Aplicativo local para converter PDFs em arquivos `.md` estruturados, com interface gráfica moderna baseada em Chromium (Microsoft Edge WebView2) e suporte a conversão rápida pelo menu de contexto do Windows. Não monitora diretórios, não agenda tarefas e não altera os PDFs originais.

Esta pasta é a base oficial do projeto. O executável Windows deve ser gerado a partir daqui.

## Instalação no disco D

1. Tenha o [uv](https://docs.astral.sh/uv/) e o Python 3.14 disponíveis. Se necessário, execute `uv python install 3.14`.
2. Abra o PowerShell na pasta deste projeto e execute:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\instalar_no_d.ps1
   ```

   Para usar outra pasta no D, informe-a: `.\instalar_no_d.ps1 -Destino "D:\NexoJuris Conversor"`.
3. Abra `D:\NexoJuris Conversor\iniciar.vbs`. Ele inicia a interface gráfica nativa em Chromium sem abrir janelas de console.

O instalador cria um ambiente Python 3.14 isolado em `D:\NexoJuris Conversor\.venv` e instala dependências com versões travadas a partir de `requirements.lock.txt`. Não há download de modelos de IA, Docker, PyTorch ou uso de GPU.

Para reconstruir o ambiente e remover dependências antigas, com o aplicativo fechado, execute `.\instalar_no_d.ps1 -RecriarAmbiente`.

## Recursos e Uso

- **Drag & Drop e Seleção Múltipla**: Arraste PDFs diretamente do Windows Explorer para o aplicativo ou selecione múltiplos arquivos pelo botão nativo.
- **Design Glassmorphism Moderno**: Interface em tons de azul e sky com identidade visual **NexoJuris** (*Conhecimento Conectado*), efeitos de vidro translúcido, métricas em tempo real e feedback sonoro leve (Web Audio API).
- **Visualizador de Markdown Integrado**: Aba de pré-visualização para inspecionar o Markdown renderizado na hora, com tabelas, citações e imagens extraídas.
- **Normalização de Imagens**: As imagens extraídas ficam em `images/`, em uma subpasta curta e segura derivada do nome do PDF, com links relativos compatíveis com Obsidian e leitores Markdown padrão.
- **Opções Avançadas de Estruturação**:
  - **Gerar partes**: cria `Nome-do-PDF_partes/parte_001.md`, respeitando títulos `#` e `##` quando o arquivo ultrapassa o limite escolhido (`60.000` caracteres por padrão).
  - **Perfil de normalização de títulos**:
    - **Boletim de jurisprudência (STJ/STF)**: reconhece ramos do direito como `#1`, rótulo "COMENTÁRIO" como `#2` e dispositivos legais como `#3`.
    - **Material de curso**: preserva numeração hierárquica (`1.`, `1.1.`, `A.`, `a)`, `i)`...) e rebaixa falsos títulos em corpo de texto.
- **Arquitetura Modular da API Bridge**: A interface Python-JavaScript foi modularizada no pacote `api_bridge/`, decompondo a camada em sete mixins especializados (`ConversionMixin`, `EditorMixin`, `LibraryMixin`, `OnlineMixin`, `ReaderMixin`, `ResourceHelperMixin` e `SystemMixin`), com orquestração central transparente via `BridgeApi` em `web_api.py`.
- **Controle de Fila e Warm Worker Pool**: **Pausar** suspende a fila antes do próximo PDF; **Parar** encerra imediatamente os processos isolados ativos. No Windows, a conversão em lote utiliza um pool aquecido de workers (`idle_pools`), reaproveitando instâncias de processos isolados e poupando de 60% a 80% do overhead de chamadas repetidas a `CreateProcessW`.
- **Encerramento e retomada**: ao fechar durante conversão ou indexação, o aplicativo pede confirmação, encerra coordenadamente os workers e preserva um journal atômico. A conversão grava checkpoint por página e oferece retomar a fila na próxima abertura sem repetir as páginas já concluídas.
- **Limites de Proteção e Resiliência de Memória**: por PDF, até 1.000 páginas, 512 MB de entrada, 5.000 imagens, 512 MB de imagens extraídas, 1,5 GB de memória e 30 minutos. A engine executa limpeza proativa de estruturas C do PyMuPDF (`fitz.Pixmap` e buffers de imagem no OCR) e desreferenciação com coleta de lixo periódica por página, evitando estouros e falsos positivos do watchdog de processo. A fila também valida o espaço livre do destino e reduz automaticamente a concorrência conforme a memória disponível.
- **Acervo resiliente**: banco, aceite de termos, licença e journal de retomada ficam no perfil gravável do usuário (`%LOCALAPPDATA%\NexoJuris\Conversor\data` no Windows), sem depender da pasta do executável. A inicialização executa `PRAGMA integrity_check`, mantém até cinco backups SQLite consistentes com rotação diária, restaura automaticamente a cópia válida mais recente e preserva bancos corrompidos em quarentena antes de reconstruir. Dados de instalações legadas são copiados sem apagar a origem; se o perfil persistente estiver indisponível, a interface abre em modo temporário identificado no diagnóstico da API. Em instalações somente leitura, a pasta de saída padrão também recua para o perfil do usuário.
- **Fidelidade verificável**: cada página recebe comparação conservadora com o mapa textual do PDF para detectar possível omissão, inversão de leitura e caracteres Unicode inválidos. Resultados de OCR são sempre marcados para conferência visual. O gate `python fidelity_corpus.py` converte um corpus versionado de documentos oficiais do STF, STJ e CNJ, além de uma digitalização inclinada, e valida hashes, páginas, âncoras semânticas ordenadas, método de extração e scores.
- **Diagnóstico de Produção e Telemetria Corporativa**: inclusive no executável `--windowed`, o aplicativo mantém logs UTF-8 persistentes em `%LOCALAPPDATA%\NexoJuris\Conversor\diagnostics`, com rotação de cinco arquivos de 2 MB e registro separado de falhas nativas. O sistema inclui rastreador de telemetria thread-safe (`TelemetryTracker`) que consolida vazão (páginas por segundo), distribuição de métodos (nativo/OCR/fallback), picos de RSS e distribuição de fidelidade em tempo de execução. Em **Manual & Central de Ajuda → Diagnóstico**, o usuário pode exportar um relatório textual sanitizado com versões, armazenamento, disco, serviços online, categorias de falha, métricas de telemetria e logs recentes, com garantia absoluta de privacidade (sem conteúdo dos PDFs, senhas ou chaves de licença).

## Preparação editorial de material de curso

O perfil **Material de curso** aplica uma etapa editorial determinística depois
da extração e antes da divisão em partes. O perfil de boletim de jurisprudência
mantém seu fluxo próprio. A preparação não resume, atualiza ou completa o Direito
exposto pelo autor, nem depende de serviços de IA.

- **Hierarquia:** mantém a numeração de assuntos e subassuntos. Quando o PDF não
  contém um título geral, as seções numéricas principais tornam-se raízes H1;
  não é criada uma capa ou atribuição bibliográfica presumida. Parágrafos em
  prosa com números e rótulos seguidos de explicações não viram títulos.
- **Destaques locais:** `ATENÇÃO!` é um bloco de nota, sem abrir outro capítulo.
  A extensão de notas com vários parágrafos não é presumida: os parágrafos
  seguintes continuam no assunto original.
- **Jurisprudência:** separa rótulos tipográficos de súmulas que ficaram colados
  à tese anterior; organiza coleções por tribunal, teses de repercussão geral,
  súmulas e temas. Referências a súmulas dentro de frases e tabelas são mantidas.
- **Continuidade:** une fragmentos entre páginas consecutivas recuperadas por
  texto nativo ou fallback quando há sinal de continuação. Preserva os comentários
  `NEXOJURIS_PAGE` e acrescenta `NEXOJURIS_CONTINUATION pages=N,M` em cada junção.
  Uma palavra repartida é completada antes do marcador da nova página; a anotação
  de continuação registra que a palavra pertence às duas páginas. Páginas ausentes,
  falhas, OCR, títulos e novas listas impedem a junção automática.
- **Palavras e tabelas:** a correção de hifenização usa as quebras do mapa textual
  do PDF, inclusive quando o Markdown interrompe a palavra com estilos ou `<br>`.
  Hífens de compostos e pronomes são preservados por evidência textual e regras
  conservadoras. Quebras ambíguas não são resolvidas por adivinhação. Células e
  quebras de linha que não interrompem palavras são preservadas.
- **Imagens:** o mapa de layout e os pixels identificam recortes quase vazios
  na margem direita. Não se remove uma figura apenas por seu tamanho. Fórmulas,
  figuras no corpo, diagramas e referências remotas são preservados.
- **Conferência:** `ConversionResult.editorial_stats` informa as operações e
  `editorial_issues` registra possíveis fragmentações restantes; a API envia
  ambos e a interface apresenta os avisos no log. Isso complementa a comparação
  textual por página, que não constitui certificação de fidelidade jurídica.

`course_editorial.build_section_index(markdown)` fornece uma árvore de seções
com identificador, pai, título, contexto ancestral, linha e páginas de início e
fim. Ela pode ser usada para chunking por assunto sem duplicar o conteúdo ou
alterar referências. Comentários de proveniência devem ser tratados como
metadados pelo consumidor de IA. Sublinhado e marca-texto usam HTML inline, que
depende do suporte do leitor Markdown. Código cercado por fences e código inline
não recebem alterações editoriais.

Regressões sintéticas: `python -m unittest discover -s tests -p test_course_editorial.py`.
Uma aprovação dos testes não substitui inspeção do documento convertido.

## Exportação automática: Markdown, estrutura e leitura

Disponível a partir da versão **1.6.0**. Consulte as
[notas da versão](docs/RELEASE_NOTES-v1.6.0.md).

Toda conversão concluída, nos dois perfis de títulos, gera na mesma pasta:

- `documento.md`: conteúdo integral, com a hierarquia e a proveniência de páginas.
- `documento.json`: índice de seções com IDs, pais, títulos, contexto ancestral,
  linhas e intervalos de páginas físicas, além da cobertura da extração, alertas
  editoriais e hashes SHA-256 do PDF e do Markdown final. Não duplica o texto.
- `documento.html`: leitura offline com índice lateral, tabelas, notas, destaques,
  layout adaptável e impressão. O conteúdo, Marked, DOMPurify e suas licenças
  ficam incorporados ao arquivo; não precisa de servidor nem do JSON para abrir.
  Quando houver imagens, mantenha a pasta `images/` junto dos arquivos exportados.

O HTML é uma fotografia da conversão. Alterações manuais posteriores no Markdown
não atualizam seus companheiros; o hash do JSON permite detectar essa divergência.
O nome vem do arquivo de origem, sem presumir capa, autoria ou título editorial.
Os formatos compartilham o conteúdo final e os IDs da árvore, preservando os
avisos de páginas não recuperadas ou que precisam de conferência. Imagens remotas,
scripts e links locais externos encontrados no conteúdo não são executados no leitor.

O conversor reserva o conjunto completo, acrescentando `(2)`, `(3)` etc. caso
qualquer companheiro já exista. Prepara todos os formatos antes de promovê-los e
publica o Markdown por último como marcador de conclusão. Erros controlados
removem os artefatos da tentativa; uma interrupção abrupta entre renomeações pode
deixar companheiros órfãos, que serão preservados e não sobrescritos na retomada.
A divisão opcional continua gerando as partes em Markdown; JSON e HTML representam
o documento completo, inclusive em reprocessamento de páginas selecionadas.

### Proteção de identificação pessoal na fonte

Disponível a partir da versão **1.6.1**. Consulte as
[notas da versão](docs/RELEASE_NOTES-v1.6.1.md).

A proteção textual atua nos dois perfis, antes de gravar checkpoints e depois
da preparação editorial. O conversor identifica campos de cadastro nas margens
do PDF (por exemplo, CPF, nome, e-mail e telefone), usando repetição e agrupamento
de identificação para distinguir marcas pessoais de contatos editoriais isolados.
Remove os campos reconhecidos do Markdown, das partes, do JSON e do conteúdo
incorporado ao HTML. O JSON registra somente contagens em `privacy`, sem os valores.
Nomes e números distintos nos exemplos jurídicos são preservados.

O PDF original permanece intacto. Se a inspeção textual falhar, a conversão é
interrompida com erro genérico. Checkpoints anteriores à política são recalculados;
exportações antigas precisam ser convertidas novamente. Esta proteção é voltada
a marcas textuais reconhecidas: não é anonimização geral de documentos, imagens
ou dados presentes apenas em digitalizações. Confira o resultado antes de compartilhar.

Regressões: `python -m unittest discover -s tests -p test_document_privacy.py`.

Para manter a extração editorial consistente, páginas de um mesmo PDF são
processadas sequencialmente: PyMuPDF não suporta extração simultânea em threads.
O parâmetro Python legado `page_workers` permanece aceito por compatibilidade,
sem habilitar threads. O paralelismo de documentos em processos isolados permanece.

Testes dos novos formatos:
`python -m unittest discover -s tests -p test_document_exports.py` e
`node tests/reading_export.test.js`.

## Release Windows

Para criar a versão executável autônoma em pasta, execute `.\build_release.ps1`. Esse é o fluxo oficial: ele chama o wrapper compatível `build_app.py`, reutiliza a implementação única em `build_support.py` e executa o PyInstaller fixado pelo lock com `uv --frozen`. O comando funciona mesmo quando chamado a partir de outro diretório.

O resultado contém duas distribuições separadas: o Conversor em
`release/dist/NexoJuris Conversor/` e o Admin privado em
`release/dist/NexoJuris Licenças Admin/`. Nenhuma chave privada é incorporada
ao pacote administrativo.

- **Compilação Nativa e Isolamento de Artefatos**: O script `compile_native.py` compila os módulos sensíveis em extensões C (`.pyd`) via Cython isolando artefatos intermediários em `build/native` e na pasta de release, com suporte à opção `--clean` para expurgar resíduos e garantir que nenhum binário de desenvolvimento permaneça na raiz do projeto. O pipeline de build (`build_support.py`) orquestra a compilação, ofuscação do frontend e empacotamento com validação rigorosa.

O CI possui um job em `windows-2025` que recompila o artefato e executa `scripts/windows_release_gate.ps1`. O gate roda o executável empacotado, valida runtime/WebView2 e `multiprocessing` com `spawn`, mantém a interface ativa por 60 segundos, verifica memória, bloqueio do executável em uso e arquivo NTFS somente leitura, e simula instalação limpa, atualização e desinstalação preservando os dados do perfil. O relatório JSON e a release aprovada são publicados como artefatos. A assinatura Authenticode é sempre inspecionada; para torná-la impeditiva, configure os secrets `WINDOWS_SIGNING_REQUIRED=true`, `WINDOWS_SIGNING_CERTIFICATE_BASE64` e `WINDOWS_SIGNING_CERTIFICATE_PASSWORD`. Esse gate cobre a distribuição PyInstaller atual; o futuro pacote MSIX/Microsoft Store deverá ter um gate próprio.

Depois, `.\criar_atalho.ps1` cria um atalho com ícone na Área de Trabalho.

## Administração local de licenças

O painel offline é distribuído por instalador privado separado. O launcher
`.\abrir_admin_licencas.ps1` prioriza o executável autocontido e mantém o modo de
desenvolvimento como fallback. A chave privada criptografada não integra o
instalador: na primeira execução, use **Configurar chave privada** para selecionar
o PEM criptografado. O Admin valida a senha e a correspondência com a chave
pública do Conversor antes de copiá-lo para
`%LOCALAPPDATA%\NexoJuris\LicencasAdmin\nexojuris_ed25519_private.pem`. Também é
possível indicar o caminho por `NEXOJURIS_ADMIN_PRIVATE_KEY`. O banco permanece
no mesmo perfil.

## Conversão rápida ("Enviar para")

Depois de gerar a release, execute `.\criar_atalho_envio_rapido.ps1` para adicionar "NexoJuris - Converter para Markdown" ao menu **Enviar para** do Windows Explorer (clique com o botão direito num ou mais PDFs). Esse modo converte direto para a pasta `PDFs Convertidos` da release e mostra um resumo em popup — sem abrir a janela principal.

- **Licenciamento ACT4**: O cliente libera Conversor, Leitor e OCR somente após importar um arquivo `.nxjlic` ACT4 válido para o fingerprint `NXJ2`. Chaves ACT2/ACT3 permanecem verificáveis apenas pelo fluxo explícito de migração do Admin e não liberam o cliente.
- **Aceite de Termos Versionado**: O aceite dos Termos de Uso é armazenado com a versão vigente (`CURRENT_TERMS_VERSION = "1.0"`). Alterações materiais na versão dos termos exigem reaceite formal do usuário.
- **Modalidades de Salvamento de Edição**: Funções de edição de PDF (rotação de páginas, gravação de anotações e post-its nativos, e aplicação/remoção de proteção AES-256) oferecem duas opções de destino:
  - **Salvar no original**: Gera e valida uma nova cópia, mantém ao lado do documento um backup recuperável com o sufixo `.nexojuris-backup.pdf` e só então substitui o original atomicamente. Assinaturas digitais devem ser revalidadas após qualquer alteração.
  - **Salvar como cópia**: Grava as edições em um novo PDF escolhido por diálogo nativo, autorizado por identificador opaco, e o registra automaticamente no acervo de recursos do aplicativo.
- **Limites Defensivos e Serviços Online**: Renderização, recortes e edições possuem orçamentos backend superiores aos controles da UI. Tradução e TTS longos são processados em blocos, sem truncamento silencioso, com prazo global, até três tentativas com backoff e circuit breaker independente por serviço. O estado operacional é exposto à interface; como os provedores externos não oferecem garantia contratual ao aplicativo, esses recursos permanecem inadequados para uso com prazo crítico.
- **Build e Compatibilidade Administrativa**: O build possui implementação única com comandos históricos preservados. A exportação administrativa aceita somente chave privada PEM criptografada e compatível com a chave pública incorporada ao cliente.

## Observações

- `PDFs Convertidos/` é a pasta de saída local padrão.
- `release/`, `.venv/`, `.uv-cache/` e caches Python são tratados como artefatos locais.
- O PyMuPDF4LLM é distribuído sob AGPL ou licença comercial. Verifique a compatibilidade da licença antes de redistribuir o aplicativo.
