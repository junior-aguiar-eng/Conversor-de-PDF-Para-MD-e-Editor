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
- **Controle de Fila**: **Pausar** suspende a fila antes do próximo PDF e **Parar** encerra a fila após o término do PDF em processamento.
- **Limites de Proteção**: Limite de 1.000 páginas por arquivo para garantir conversões leves e seguras.

## Release Windows

Para criar a versão executável autônoma em pasta, execute `.\build_release.ps1`. O script tenta nesta ordem:

1. Reutilizar o `PyInstaller` já instalado na `.venv`, se existir.
2. Pedir ao `uv` para executar o `PyInstaller` junto das dependências travadas do projeto.

O resultado fica em `release/dist/NexoJuris Conversor/`. O executável principal fica em `release/dist/NexoJuris Conversor/NexoJuris Conversor.exe`.

Depois, `.\criar_atalho.ps1` cria um atalho com ícone na Área de Trabalho.

## Conversão rápida ("Enviar para")

Depois de gerar a release, execute `.\criar_atalho_envio_rapido.ps1` para adicionar "NexoJuris - Converter para Markdown" ao menu **Enviar para** do Windows Explorer (clique com o botão direito num ou mais PDFs). Esse modo converte direto para a pasta `PDFs Convertidos` da release e mostra um resumo em popup — sem abrir a janela principal.

- **Licenciamento e Impressão Digital Evolved**: Ativações do software utilizam fingerprints e chaves Ed25519 versionadas. Licenças legadas v1 (`NXJ-` / `ACT2-01-`) permanecem 100% suportadas e operacionais, enquanto novas ativações utilizam o fingerprint estável v2 (`NXJ2-` / `ACT3-01-`). Instalações ativas são migradas transparentemente sem invalidar chaves prévias. O emissor administrativo `admin_keygen.py` gera e analisa ambas as versões.
- **Aceite de Termos Versionado**: O aceite dos Termos de Uso é armazenado com a versão vigente (`CURRENT_TERMS_VERSION = "1.0"`). Alterações materiais na versão dos termos exigem reaceite formal do usuário.
- **Modalidades de Salvamento de Edição**: Funções de edição de PDF (rotação de páginas, gravação de anotações e post-its nativos, e aplicação/remoção de proteção AES-256) oferecem duas opções de destino:
  - **Salvar no original**: Sobrescreve o arquivo original de forma segura (preservando assinaturas e estrutura através de salvamento incremental quando aplicável).
  - **Salvar como cópia**: Grava as edições em um novo arquivo PDF escolhido pelo usuário e o registra automaticamente no acervo de recursos do aplicativo.

## Observações

- `PDFs Convertidos/` é a pasta de saída local padrão.
- `release/`, `.venv/`, `.uv-cache/` e caches Python são tratados como artefatos locais.
- O PyMuPDF4LLM é distribuído sob AGPL ou licença comercial. Verifique a compatibilidade da licença antes de redistribuir o aplicativo.
