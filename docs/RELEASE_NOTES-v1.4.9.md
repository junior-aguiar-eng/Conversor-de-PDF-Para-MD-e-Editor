# NexoJuris Conversor v1.4.9

## Modernização Arquitetural, Observabilidade e Resiliência Corporativa

Esta versão consolida o processo de refatoração, desacoplamento e robustecimento de nível corporativo do aplicativo, mantendo 100% de compatibilidade retroativa e zero regressões funcionais.

---

### 1. Decomposição Modular da Camada Bridge (`api_bridge/`)

- **Modularização Estrutural**: O arquivo monolítico `web_api.py` (anteriormente com mais de 3.200 linhas) foi reduzido para uma fachada concisa de 247 linhas, transferindo suas responsabilidades para o novo pacote `api_bridge/`.
- **Sete Mixins Especializados**:
  - `ResourceHelperMixin`: Resolução segura de caminhos e controle de acesso via tokens opacos.
  - `ConversionMixin`: Fila de conversão, journals de recuperação atômicos e paralelismo.
  - `EditorMixin`: Operações com Markdown, preview isolado e anotações geométricas.
  - `LibraryMixin`: Gerenciamento do catálogo SQLite com FTS5, favoritos e busca.
  - `OnlineMixin`: Circuit breaker e resiliência em tradução e síntese de voz (TTS).
  - `ReaderMixin`: Renderização segura e busca vetorial em páginas de PDF.
  - `SystemMixin`: Diálogos nativos do sistema operacional, licença ACT4 e diagnósticos.
- **Compatibilidade Plena**: A classe `BridgeApi` mantém idêntica superfície de métodos para a interface gráfica Chromium via `window.pywebview.api`.

---

### 2. Resiliência Operacional e Eliminação de Process Churn no Windows

- **Warm Worker Pool (`idle_pools`)**: Reuso de subprocessos isolados saudáveis de `ProcessPoolExecutor`, reduzindo em até 80% o overhead e latência de `CreateProcessW` em conversões consecutivas em lote.
- **Otimização de Polling de Controle**: Verificação de arquivos de sinalização (`.pause` e `.stop`) protegida por cache baseado em `st_mtime_ns`, evitando contenção de I/O em disco.
- **Tratamento de Locks NTFS**: Ajuste de concorrência na criação e limpeza atômica de diretórios temporários contra bloqueios transitórios de arquivos no Windows.

---

### 3. Gestão Avançada de Memória e Prevenção do Watchdog

- **Destruição Imediata de Objetos C do PyMuPDF**: Implementação de destruição explícita (`del pix`) no motor OCR (`ocr_engine.py`) para acionar o desalocador C `fz_drop_pixmap` imediatamente após o uso, além da liberação pontual de buffers PNG em memória.
- **Coleta de Lixo Pró-Ativa no Conversor**: Desreferenciação de páginas (`del page`) e disparo de coleta de lixo (`gc.collect()`) a cada 25 páginas ou quando a memória RSS do processo atinge 250 MB, prevenindo falsos positivos de estouro de memória em PDFs jurídicos com centenas de páginas.

---

### 4. Telemetria Corporativa e Diagnóstico de Produção

- **`TelemetryTracker` Thread-Safe**: Adicionado coletor centralizado em `production_diagnostics.py` para medição de vazão operacional (páginas por segundo), distribuição de métodos de extração (nativo, OCR, fallback), consumo de pico de RSS e distribuição de índices de fidelidade.
- **Relatório de Suporte Aprimorado**: Inclusão automática das métricas de telemetria da sessão nos relatórios exportados em **Manual & Central de Ajuda → Diagnóstico**.
- **Privacidade por Design**: Nenhuma informação textual de documentos ou caminhos de arquivos do usuário é coletada ou exportada na telemetria.

---

### 5. Higiene de Build e Qualidade de Código

- **Isolamento de Extensões Nativas**: Atualização de `compile_native.py` e `build_support.py` com isolamento estrito de artefatos compilados C (`.pyd`) e switch `--clean`, mantendo a raiz do repositório sempre limpa.
- **Testes e Validação Contínua**: 266/266 testes automatizados aprovados na suíte de testes de regressão, integração e contratos funcionais.
