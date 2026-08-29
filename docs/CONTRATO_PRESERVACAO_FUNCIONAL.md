# Contrato de preservação funcional

Este contrato é vinculante para as fases de correção. Nenhuma capacidade listada abaixo pode ser removida, desabilitada, limitada ou tornada inacessível como forma de corrigir defeitos sem decisão específica de produto. Correções de segurança, integridade e desempenho devem substituir o mecanismo interno e preservar o comportamento legítimo.

## Capacidades protegidas

- converter integralmente PDFs de até 1.000 páginas, em execução individual, lote e paralelo;
- converter PDFs vetoriais, digitalizados, híbridos e protegidos, mediante senha quando necessária;
- gerar um Markdown único ou partes, preservando imagens, títulos, links, tabelas, código e caracteres especiais;
- adicionar arquivos por seleção, arrastar e soltar, “Enviar para”, linha de comando, recentes e acervo;
- abrir PDFs extensos independentemente da conversão e carregar páginas sob demanda;
- manter anotações, marca-texto, texto, desfazer, rotação, proteção e desproteção por documento;
- salvar alterações no original quando expressamente escolhido e também permitir salvar cópia;
- manter busca, marcadores, página, zoom, histórico e registros de documentos temporariamente indisponíveis;
- manter tradução e síntese de voz, com tratamento funcional da indisponibilidade de rede;
- validar licenças já emitidas durante qualquer evolução do fingerprint;
- preservar instalação no disco D, release PyInstaller, atalhos e identidade visual atual.

Leitura, indexação e conversão são subsistemas independentes. A abertura não depende de indexação integral; a indexação pode ser incremental ou explícita; a conversão continua percorrendo todas as páginas autorizadas até o limite de 1.000.

## Classificação inicial

São capacidades deliberadas: o limite inclusivo de 1.000 páginas; conversão integral em uma chamada; paralelismo entre documentos; OCR local seletivo; edição do PDF original; reabertura por caminhos externos legítimos; persistência de leitura; serviços opcionais de tradução e voz; licenciamento offline; os dois fluxos de build existentes.

São defeitos conhecidos, e não justificativas para reduzir essas capacidades: escaping ausente ou inconsistente no renderer; estado global de anotações; respostas assíncronas obsoletas; inicialização repetível com efeitos duplicados; parser Markdown por expressões regulares e HTML ativo; dependências remotas da interface; operações privilegiadas baseadas diretamente em caminhos; colisões de destino em lotes homônimos; perda silenciosa de cobertura de páginas; cortes estruturais nas partes; escritas não transacionais; abertura e indexação integrais acopladas; cache sem orçamento de memória; persistência dependente da ordem de indexação; exclusão de histórico por indisponibilidade temporária; incompatibilidades futuras de licença; aceite sem versionamento efetivo; ausência de “Salvar cópia”; limites defensivos e recursos de áudio incompletos; duplicações no build e código residual ainda não auditado.

## Matriz da Fase 0

| Cenário | Evidência automatizada atual | Situação inicial |
|---|---|---|
| Seleção e drag-and-drop | `test_functional_baseline.py` executa os dois fluxos em JavaScript | Coberto |
| PDF sintético de 1.000 páginas | `test_functional_baseline.py` | Coberto |
| Homônimos em paralelo | `test_functional_baseline.py` | Defeito reproduzido: destinos ainda não são reservados |
| Vetorial, digitalizado e híbrido | `test_functional_baseline.py`, `test_converter.py` e `test_ocr_engine.py` | Coberto com extração simulada e PDFs sintéticos |
| PDF protegido | `test_functional_baseline.py` e `test_web_api.py` | Leitura coberta; conversão ainda incompatível |
| Abrir PDF extenso sem converter | `test_functional_baseline.py` | Capacidade coberta; abertura ainda materializa dados de todas as páginas |
| Troca de documento com anotações pendentes | `test_functional_baseline.py` | Defeito reproduzido: estado pendente permanece global |
| Anotações em várias páginas | `test_functional_baseline.py` | Coberto no backend |
| Preview Markdown legítimo e malicioso | `test_functional_baseline.py` | Elementos legítimos preservados; injeção por atributo reproduzida |
| Histórico, zoom e marcadores após reinício | `test_functional_baseline.py` e `test_library_db.py` | Coberto no backend |
| Documento temporariamente indisponível | `test_functional_baseline.py` | Preservação coberta; classificação explícita ainda pendente |
| Licença legada | `test_functional_baseline.py` | Formato HMAC anterior reconstruído do histórico e incompatibilidade reproduzida |
| Tradução e TTS com/sem rede | `test_web_api.py` e `test_functional_baseline.py` | Coberto no backend com serviços simulados |
| Build, instalação e início da release | `test_installer.py` e `scripts/verify_release_start.ps1` | Executar a cada fechamento da Fase 0 |

Os cenários pendentes exigem fixtures sintéticas ou ambientes descartáveis. Bancos, PDFs e licenças reais do usuário não serão usados nos testes.

## Fechamento da Fase 0 — 28/08/2026

A baseline foi concluída com 100 testes aprovados, lint Python sem ocorrências, validação sintática de `web/app.js`, build PyInstaller limpo e inicialização bem-sucedida por oito segundos a partir de uma cópia nova da release. O ensaio de inicialização encerrou somente o processo descartável que ele próprio criou.

Foram reproduzidos, sem corrigir prematuramente nem normalizar como comportamento desejado: colisão de destino entre homônimos; compartilhamento de anotações ao trocar de documento; injeção de atributo no preview Markdown; ausência de senha no fluxo de conversão protegida; incompatibilidade com licenças HMAC anteriormente emitidas; materialização integral das páginas na abertura do leitor. Esses resultados alimentam, respectivamente, as Fases 3, 1, 2, 3, 5 e 4.

Todos os arquivos, bancos, PDFs e licenças empregados na baseline foram sintéticos ou descartáveis. Nenhum dado real do usuário foi modificado.
