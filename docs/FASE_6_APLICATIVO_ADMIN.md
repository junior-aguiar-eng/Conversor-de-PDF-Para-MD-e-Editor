# Fase 6 — Aplicativo NexoJuris Licenças Admin

Status: concluída em 30/08/2026.

## Entregas

- aplicativo PyWebView independente, iniciado por `license_admin_app.py`;
- interface local em `admin_web`, sem recursos externos nem handlers inline;
- tela inicial search-first, sem carregamento automático de tabela geral;
- painel com atividades recentes, licenças expirando, aguardando conexão, suspensas e revogadas;
- busca FTS5 com normalização Unicode e paginação estável;
- pesquisa por `license_id`, nome, e-mail, telefone, CPF/CNPJ, `machine_id`, referência comercial, status e validade;
- filtros `status:expirada`, `status:revogada`, `status:suspensa`, `status:aguardando`, `expira:Nd`, `offline:Nd`, `cliente:` e `maquina:`;
- detalhe individual com cliente, estado efetivo, plano, funcionalidades, dispositivo, validade, última conexão, prazo offline e histórico;
- emissão de licença com prazos de 3, 6 ou 12 meses e vinculação atômica do dispositivo;
- ações de renovar, suspender, reativar, revogar, vincular ou trocar dispositivo e exportar `.nxjlic`;
- senha da chave privada solicitada apenas na exportação e não persistida pelo bridge;
- frases de confirmação `REVOGAR:<license_id>` e `TROCAR:<license_id>` preservadas até o núcleo administrativo.

## Gate de validação

- base sintética: 1.500 licenças, nomes repetidos e combinações de status, validade e leases;
- consulta vazia: zero registros e nenhuma tabela geral;
- paginação de 750 homônimos: páginas distintas e IDs preservados;
- plano SQLite: uso confirmado de índice virtual FTS5;
- filtros de status, expiração e prazo offline: resultados exatos;
- confirmação inválida no bridge: recusada pelo núcleo;
- emissão inválida: recusada antes da criação do cliente;
- `python -m unittest discover -s tests -p 'test_*.py'`: 258 testes aprovados;
- testes frontend: 10 aprovados;
- `ruff check .`, `py_compile`, `node --check` e `git diff --check`: aprovados.

As Fases 4, 5 e 6 formam a unidade de commit desta etapa.
