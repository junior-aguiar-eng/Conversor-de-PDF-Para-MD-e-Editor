# Fase 10 — Segurança operacional

## Controles implementados

- Chaves Ed25519 privadas são aceitas somente em PEM PKCS#8 criptografado.
- O chaveiro operacional separa rigidamente `test` e `production`; o ambiente também fica vinculado ao banco.
- Cada finalidade (`license` e `lease`) possui um `key_id` ativo. Chaves aposentadas permanecem disponíveis para validar e reexportar documentos antigos.
- O backup offline de chaves usa AES-256-GCM, senha derivada por Scrypt e restauração vinculada ao ambiente.
- O banco administrativo já possui backup AES-256-GCM, verificação de integridade e restauração atômica.
- Senhas administrativas usam Scrypt com salt individual.
- O serviço remoto exige token revogável e TOTP. A revogação da conta invalida todos os seus tokens.
- Em produção, o serviço exige o chaveiro rotacionável e chave TLS privada criptografada, cuja senha vem de variável de ambiente.
- Eventos administrativos formam uma cadeia SHA-256 e as tabelas de auditoria têm bloqueio de `UPDATE` e `DELETE`.

## Convenções obrigatórias

Os `key_id` novos devem identificar finalidade e ambiente:

- teste: `license-test-AAAA-MM-*` e `lease-test-AAAA-MM-*`;
- produção: `license-prod-AAAA-MM-*` e `lease-prod-AAAA-MM-*`.

As senhas não devem aparecer em argumentos, arquivos ou logs. Os comandos recebem apenas o nome de uma variável de ambiente. O backup de chaves deve usar senha diferente das chaves e do banco.

## Inicialização e rotação

Inicializar uma chave somente quando ainda não existir chave ativa para a finalidade:

```powershell
python license_security_app.py --environment production --key-store D:\NexoJuris\keys initialize --purpose license --key-id license-prod-2026-09-a --password-env NEXOJURIS_LICENSE_KEY_PASSWORD
```

Rotacionar exige confirmação vinculada à finalidade e ao ambiente:

```powershell
python license_security_app.py --environment production --key-store D:\NexoJuris\keys --audit-database D:\NexoJuris\admin.db --admin-user-id ADM-... rotate --purpose license --key-id license-prod-2027-01-b --password-env NEXOJURIS_LICENSE_KEY_PASSWORD --confirmation ROTACIONAR:license:production
```

Após a rotação, exportar o conjunto de chaves públicas e incorporá-lo ao cliente antes de emitir licenças com o novo `key_id`:

```powershell
python license_security_app.py --environment production --key-store D:\NexoJuris\keys export-public-keys --purpose license --destination D:\NexoJuris\public-license-keys.json
```

## Backup offline e recuperação

Criar o backup em mídia removível mantida desconectada e conferir uma restauração em diretório vazio:

```powershell
python license_security_app.py --environment production --key-store D:\NexoJuris\keys backup --destination E:\offline\production-keys.nxjkeys --password-env NEXOJURIS_KEY_BACKUP_PASSWORD
python license_security_app.py --environment production --key-store D:\NexoJuris\keys-restored restore --source E:\offline\production-keys.nxjkeys --password-env NEXOJURIS_KEY_BACKUP_PASSWORD --confirmation RESTAURAR-CHAVES:production
```

A recuperação operacional exige restaurar separadamente o banco administrativo e o chaveiro. A restauração do banco usa `RESTAURAR:<nome-do-banco>` e valida esquema e integridade antes da substituição atômica.

```powershell
python license_security_app.py --environment production --key-store D:\NexoJuris\keys --admin-user-id ADM-... backup-database --database D:\NexoJuris\admin.db --destination E:\offline\production-admin.nxjbackup --password-env NEXOJURIS_DATABASE_BACKUP_PASSWORD
python license_security_app.py --environment production --key-store D:\NexoJuris\keys --admin-user-id ADM-... restore-database --database D:\NexoJuris\admin.db --source E:\offline\production-admin.nxjbackup --password-env NEXOJURIS_DATABASE_BACKUP_PASSWORD --confirmation RESTAURAR:admin.db
```

## Chave comprometida

1. Interromper emissão e cópia do chaveiro afetado.
2. Preservar evidências e registrar o incidente.
3. Marcar a chave comprometida com `COMPROMETIDA:<key_id>`; ela deixa de ser carregada ou exportada como confiável.
4. Rotacionar para um `key_id` novo e distribuir previamente a chave pública correspondente.
5. Identificar licenças e leases assinados pelo `key_id` comprometido, revogar ou reemitir conforme o alcance confirmado.
6. Trocar senhas de chave, backup, banco, token administrativo e segundo fator quando houver possibilidade de exposição.

## Gate da fase

Os testes automatizados cobrem perda e restauração do banco, restauração do chaveiro offline, rotação com preservação das chaves aposentadas, bloqueio de ambiente divergente, marcação de chave comprometida, senha administrativa, TOTP remoto, revogação de token/conta e adulteração da auditoria.
