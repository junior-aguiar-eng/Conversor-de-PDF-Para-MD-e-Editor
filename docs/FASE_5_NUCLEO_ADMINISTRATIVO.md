# Fase 5 — Núcleo administrativo e banco de licenças

Status: concluída em 30/08/2026, sem commit isolado.

## Entregas

- pacote `admin_licensing` como núcleo principal; `admin_keygen.py` preservado como contingência legada;
- banco SQLite versionado com `customers`, `licenses`, `licensed_devices`, `license_features`, `license_renewals`, `license_status_changes`, `offline_exports`, `online_leases`, `audit_events` e `admin_users`;
- criação de cliente e usuário administrativo;
- emissão com prazos comerciais restritos a 3, 6 ou 12 meses;
- vinculação e troca confirmada de computador;
- renovação, suspensão, revogação confirmada e irreversível e reativação de licença suspensa;
- exportação `.nxjlic` ACT4 assinada e vinculada ao dispositivo ativo;
- consulta consolidada da licença e histórico administrativo;
- identificadores imutáveis por chave primária e triggers contra alteração;
- históricos de status, renovação, exportação e auditoria protegidos contra `UPDATE` e `DELETE`;
- auditoria append-only encadeada por SHA-256, com verificação integral da cadeia;
- conexões curtas, WAL, `busy_timeout` e transações `BEGIN IMMEDIATE` para emissão concorrente;
- backup criptografado com Scrypt e AES-256-GCM, restauração autenticada e validação de integridade/esquema;
- carregamento sob demanda de chave Ed25519 exclusivamente de PEM criptografado, sem persistência da senha no banco.

## Confirmações destrutivas

- revogação: `REVOGAR:<license_id>`;
- troca do dispositivo ativo: `TROCAR:<license_id>`;
- restauração do banco: `RESTAURAR:<nome-do-banco>`.

## Limites preservados

- a chave privada não integra o banco, os backups ou os arquivos do cliente;
- o banco administrativo não é distribuído com o Conversor;
- `admin_keygen.py` continua disponível para contingência ACT2/ACT3;
- interface search-first pertence à Fase 6;
- leases e validação remota serão operacionalizados na Fase 7.

## Gate de validação

- `python -m unittest discover -s tests -p 'test_*.py'`: 251 testes aprovados;
- suíte específica da Fase 5: 7 testes aprovados;
- emissão concorrente em oito threads: IDs únicos e auditoria íntegra;
- backup sem assinatura SQLite nem `license_id` em texto claro;
- restauração com senha incorreta e sem confirmação: recusada;
- restauração válida: persistência e cadeia de auditoria aprovadas;
- chave privada não criptografada: recusada;
- exportação ACT4: assinatura, máquina, funcionalidades e prazo verificados;
- `ruff check .`, `py_compile` e `git diff --check`: aprovados;
- 6 testes frontend aprovados.

O commit permanece adiado para o fechamento conjunto das Fases 4, 5 e 6.
