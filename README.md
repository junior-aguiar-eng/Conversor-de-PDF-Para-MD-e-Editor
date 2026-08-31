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
- **Controle de Fila**: **Pausar** suspende a fila antes do próximo PDF; **Parar** encerra imediatamente os processos isolados ainda ativos.
- **Encerramento e retomada**: ao fechar durante conversão ou indexação, o aplicativo pede confirmação, encerra coordenadamente os workers e preserva um journal atômico. A conversão grava checkpoint por página e oferece retomar a fila na próxima abertura sem repetir as páginas já concluídas.
- **Limites de Proteção**: por PDF, até 1.000 páginas, 512 MB de entrada, 5.000 imagens, 512 MB de imagens extraídas, 1,5 GB de memória e 30 minutos. A fila também valida o espaço livre do destino e reduz automaticamente a concorrência conforme a memória disponível.
- **Acervo resiliente**: banco, aceite de termos, licença e journal de retomada ficam no perfil gravável do usuário (`%LOCALAPPDATA%\NexoJuris\Conversor\data` no Windows), sem depender da pasta do executável. A inicialização executa `PRAGMA integrity_check`, mantém até cinco backups SQLite consistentes com rotação diária, restaura automaticamente a cópia válida mais recente e preserva bancos corrompidos em quarentena antes de reconstruir. Dados de instalações legadas são copiados sem apagar a origem; se o perfil persistente estiver indisponível, a interface abre em modo temporário identificado no diagnóstico da API. Em instalações somente leitura, a pasta de saída padrão também recua para o perfil do usuário.
- **Fidelidade verificável**: cada página recebe comparação conservadora com o mapa textual do PDF para detectar possível omissão, inversão de leitura e caracteres Unicode inválidos. Resultados de OCR são sempre marcados para conferência visual. O gate `python fidelity_corpus.py` converte um corpus versionado de documentos oficiais do STF, STJ e CNJ, além de uma digitalização inclinada, e valida hashes, páginas, âncoras semânticas ordenadas, método de extração e scores.
- **Diagnóstico de produção**: inclusive no executável `--windowed`, o aplicativo mantém logs UTF-8 persistentes em `%LOCALAPPDATA%\NexoJuris\Conversor\diagnostics`, com rotação de cinco arquivos de 2 MB e registro separado de falhas nativas. Exceções não tratadas da thread principal e de workers são capturadas. Em **Manual & Central de Ajuda → Diagnóstico**, o usuário pode exportar um relatório textual sanitizado com versões, armazenamento, disco, serviços online, categorias de falha e logs recentes, sem conteúdo dos PDFs, senhas ou chaves de licença.

## Release Windows

Para criar a versão executável autônoma em pasta, execute `.\build_release.ps1`. Esse é o fluxo oficial: ele chama o wrapper compatível `build_app.py`, reutiliza a implementação única em `build_support.py` e executa o PyInstaller fixado pelo lock com `uv --frozen`. O comando funciona mesmo quando chamado a partir de outro diretório.

O resultado contém duas distribuições separadas: o Conversor em
`release/dist/NexoJuris Conversor/` e o Admin privado em
`release/dist/NexoJuris Licenças Admin/`. Nenhuma chave privada é incorporada
ao pacote administrativo.

O CI possui um job em `windows-2025` que recompila o artefato e executa `scripts/windows_release_gate.ps1`. O gate roda o executável empacotado, valida runtime/WebView2 e `multiprocessing` com `spawn`, mantém a interface ativa por 60 segundos, verifica memória, bloqueio do executável em uso e arquivo NTFS somente leitura, e simula instalação limpa, atualização e desinstalação preservando os dados do perfil. O relatório JSON e a release aprovada são publicados como artefatos. A assinatura Authenticode é sempre inspecionada; para torná-la impeditiva, configure os secrets `WINDOWS_SIGNING_REQUIRED=true`, `WINDOWS_SIGNING_CERTIFICATE_BASE64` e `WINDOWS_SIGNING_CERTIFICATE_PASSWORD`. Esse gate cobre a distribuição PyInstaller atual; o futuro pacote MSIX/Microsoft Store deverá ter um gate próprio.

Depois, `.\criar_atalho.ps1` cria um atalho com ícone na Área de Trabalho.

## Administração local de licenças

O painel offline é distribuído por instalador privado separado. O launcher
`.\abrir_admin_licencas.ps1` prioriza o executável autocontido e mantém o modo de
desenvolvimento como fallback. A chave privada criptografada não integra o
instalador: deve ficar em
`%LOCALAPPDATA%\NexoJuris\LicencasAdmin\nexojuris_ed25519_private.pem` ou no caminho
indicado por `NEXOJURIS_ADMIN_PRIVATE_KEY`. O banco permanece no mesmo perfil.

## Conversão rápida ("Enviar para")

Depois de gerar a release, execute `.\criar_atalho_envio_rapido.ps1` para adicionar "NexoJuris - Converter para Markdown" ao menu **Enviar para** do Windows Explorer (clique com o botão direito num ou mais PDFs). Esse modo converte direto para a pasta `PDFs Convertidos` da release e mostra um resumo em popup — sem abrir a janela principal.

- **Licenciamento e Impressão Digital Evolved**: Ativações do software utilizam fingerprints e chaves Ed25519 versionadas. Licenças legadas v1 (`NXJ-` / `ACT2-01-`) permanecem 100% suportadas e operacionais, enquanto novas ativações utilizam o fingerprint estável v2 (`NXJ2-` / `ACT3-01-`). Instalações ativas são migradas transparentemente sem invalidar chaves prévias. O emissor administrativo `admin_keygen.py` gera e analisa ambas as versões.
- **Aceite de Termos Versionado**: O aceite dos Termos de Uso é armazenado com a versão vigente (`CURRENT_TERMS_VERSION = "1.0"`). Alterações materiais na versão dos termos exigem reaceite formal do usuário.
- **Modalidades de Salvamento de Edição**: Funções de edição de PDF (rotação de páginas, gravação de anotações e post-its nativos, e aplicação/remoção de proteção AES-256) oferecem duas opções de destino:
  - **Salvar no original**: Gera e valida uma nova cópia, mantém ao lado do documento um backup recuperável com o sufixo `.nexojuris-backup.pdf` e só então substitui o original atomicamente. Assinaturas digitais devem ser revalidadas após qualquer alteração.
  - **Salvar como cópia**: Grava as edições em um novo PDF escolhido por diálogo nativo, autorizado por identificador opaco, e o registra automaticamente no acervo de recursos do aplicativo.
- **Limites Defensivos e Serviços Online**: Renderização, recortes e edições possuem orçamentos backend superiores aos controles da UI. Tradução e TTS longos são processados em blocos, sem truncamento silencioso, com prazo global, até três tentativas com backoff e circuit breaker independente por serviço. O estado operacional é exposto à interface; como os provedores externos não oferecem garantia contratual ao aplicativo, esses recursos permanecem inadequados para uso com prazo crítico.
- **Build e Compatibilidade Administrativa**: O build possui implementação única com comandos históricos preservados. Chaves administrativas PEM podem ser criptografadas opcionalmente, sem invalidar PEMs ou licenças existentes.

## Observações

- `PDFs Convertidos/` é a pasta de saída local padrão.
- `release/`, `.venv/`, `.uv-cache/` e caches Python são tratados como artefatos locais.
- O PyMuPDF4LLM é distribuído sob AGPL ou licença comercial. Verifique a compatibilidade da licença antes de redistribuir o aplicativo.
