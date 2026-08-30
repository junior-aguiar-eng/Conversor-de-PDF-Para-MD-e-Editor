# Fase 8 — Renovação e reativação pelo Admin

Status: concluída em 30 de agosto de 2026.

## Entregas

- Renovação administrativa por 3, 6 ou 12 meses, com exibição do novo
  vencimento e confirmação explícita antes da gravação.
- Endpoint administrativo de reativação para licenças suspensas.
- Renovação online refletida no próximo lease assinado, sem nova chave manual.
- Consulta automática do Conversor a cada 24 horas e consulta imediata para
  estados que podem ser revertidos pelo Admin, como expiração e suspensão.
- Renovação offline seguida da exportação opcional imediata de um novo
  `.nxjlic`.
- Importação do `.nxjlic` renovado com substituição somente depois da validação
  integral de assinatura, máquina e estado temporal.
- Revogação percebida na próxima consulta; sem conexão, o último lease válido
  continua apenas até o fim do prazo offline já concedido.
- Troca de computador com confirmação vinculada à licença, desativação do
  dispositivo anterior, preservação do histórico e oferta de exportação ACT4
  para a nova máquina.

## Gate

`tests/test_phase8_license_lifecycle.py` cobre o ciclo ponta a ponta de
renovação online, suspensão, reativação, transferência, revogação, renovação
offline, importação segura e consulta automática.

`tests/phase8_admin_lifecycle.test.js` verifica a confirmação do vencimento, a
exportação offline e a continuidade das ações críticas na interface Admin.

O commit e o push permanecem adiados para o fechamento conjunto das Fases 7,
8 e 9.
