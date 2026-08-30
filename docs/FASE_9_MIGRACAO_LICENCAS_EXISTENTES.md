# Fase 9 — Migração das licenças existentes

Status: concluída em 30 de agosto de 2026.

## Entregas

- O Conversor continua aceitando ACT2 e ACT3 pelo caminho legado, sem prazo,
  lease ou exigência de serviço online.
- O Admin continua emitindo novas licenças exclusivamente no formato ACT4.
- A migração legada tornou-se uma operação administrativa explícita, com
  validação criptográfica da licença antiga e confirmação vinculada ao código da
  máquina.
- O registro administrativo guarda somente o hash da chave legada, a versão, a
  máquina, o cliente, a nova licença e o responsável pela migração.
- O cliente antigo não é desativado pelo registro administrativo. A substituição
  ocorre somente quando o usuário importa o arquivo ACT4 validado.
- Antes de substituir uma licença local, o Conversor arquiva a licença anterior
  no banco. Uma restauração exige a frase
  `RESTAURAR:LICENCA-ANTERIOR` e revalida o registro preservado.
- ACT4 inválido, adulterado ou pertencente a outra máquina não substitui ACT2 ou
  ACT3 existente.
- A evolução do esquema de licença e a migração dos dados de instalação são
  aditivas: banco, acervo, licença, diário de conversão e fontes legadas não são
  removidos.

## Sequência operacional preservada

1. Distribuir o Conversor compatível com ACT2, ACT3 e ACT4.
2. Disponibilizar o Admin ACT4.
3. Configurar o serviço online somente para licenças híbridas.
4. Emitir novas licenças em ACT4.
5. Migrar licenças antigas apenas mediante concordância e entrega do novo
   `.nxjlic`.

## Gate

`tests/test_phase9_license_migration.py` cobre validação ACT3, emissão ACT4,
registro sem chave legada em texto aberto, duplicidade, preservação na
atualização, ausência de dependência online, importação ACT4 e rollback
controlado.

`tests/phase9_migration_ui.test.js` verifica que a migração é apresentada como
ação explícita e não desativa silenciosamente a licença anterior.
