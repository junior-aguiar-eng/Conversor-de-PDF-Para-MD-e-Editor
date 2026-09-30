# NexoJuris Conversor v1.5.0

## Paralelismo Multi-Nível, Aceleração por Cache Inteligente e Otimização de Inferência

Esta versão introduz grandes avanços em desempenho, vazão de processamento e tempos de resposta do NexoJuris Conversor, com foco em documentos extensos e fluxos repetitivos de OCR, mantendo 100% de estabilidade e integridade funcional.

---

### 1. Paralelismo Intra-Documento em Nível de Páginas (Page-Level Parallelism)

- **Extração Concorrente de Páginas**: Em documentos extensos (> 6 páginas), o conversor distribui a extração textual, renderização e processamento de blocos entre múltiplas threads paralelas (`ThreadPoolExecutor`), explorando plenamente os núcleos de CPU disponíveis.
- **Sessões Isoladas Thread-Local do PyMuPDF**: Para evitar contenção de locks no runtime C do MuPDF, cada worker thread opera com uma instância `fitz.Document` exclusiva via `threading.local()`.
- **Propagação de Contexto e Determinismo**: Uso de instâncias isoladas de contexto (`copy_context().run`) por tarefa paralela, assegurando que o encadeamento de imagens e a ordenação sequencial do Markdown final permaneçam 100% determinísticos.
- **Auto-Ajuste Conforme o Pool**: Quando múltiplos arquivos são processados simultaneamente no lote, o conversor auto-ajusta a alocação de threads por arquivo (`page_workers`) para maximizar a vazão global sem gerar saturação de CPU ou disputa de memória.

---

### 2. Cache de OCR em Dois Níveis (Content-Addressable OCR Cache)

- **L1 (Memória RAM)**: Cache LRU rápido baseado em `OrderedDict` (capacidade de 512 entradas) com proteção de thread-safety (`threading.Lock()`), eliminando reprocessamento em loops e re-renderizações.
- **L2 (Persistência em Disco)**: Armazenamento permanente indexado por hash SHA-256 do pixmap renderizado em `%LOCALAPPDATA%\NexoJuris\Conversor\ocr_cache`. 
- **Aceleração Drástica em Arquivos Reincidentes**: Páginas de PDF escaneadas idênticas, formulários padronizados, capas de processos judiciais ou documentos reprocessados obtêm o resultado do OCR em **< 1 ms**, dispensando o processamento do modelo ONNX.

---

### 3. Detecção Instantânea de Páginas em Branco (*Blank-Page Fast-Path*)

- **Análise Amostral $O(1)$**: Verificação estatística de luminosidade (média e desvio padrão amostrado em 256 pontos da imagem).
- **Zero Overhead em Páginas Separadoras**: Se a página for comprovadamente vazia/branca, o conversor retorna imediatamente sem acionar o motor de OCR ou o modelo de deep learning, economizando de 2 a 4 segundos de inferência por página em branco.

---

### 4. Pré-Aquecimento Assíncrono do Runtime ONNX (*Background Warmup*)

- **Inicialização em Segundo Plano**: A carga dos pesos do modelo neural RapidOCR e a inicialização da engine ONNX Runtime são disparadas assincronamente em segundo plano na inicialização da interface gráfica.
- **Eliminação de Congelamento Inicial**: O primeiro documento que requer OCR na sessão não sofre mais a latência inicial de carga fria (cold-start) do runtime ONNX.

---

### 5. Telemetria e Indicadores Visuais no Frontend

- **Métricas de Economia em Tempo Real**: O backend contabiliza acessos e taxa de acerto do cache (`ocr_cache_hits` e `ocr_cache_hit_ratio`).
- **Feedback na Interface**: Notificação visual e registro no console com indicação clara de ganho: `⚡ X pág(s) aceleradas por cache`.
- **Estatísticas no Diagnóstico**: Integração total com o relatório técnico de suporte.

---

### 6. Qualidade de Código e Confiabilidade

- **272/272 Testes Aprovados**: Cobertura abrangente com suíte completa de testes unitários, testes de concorrência e integridade de pipeline.
