# Fase 7 — Serviço online de licenças

Status: concluída em 30 de agosto de 2026.

## Entregas

- API pública para `check`, `activate` e `refresh`.
- API administrativa autenticada para emissão, renovação, suspensão,
  revogação, transferência, busca e consulta de eventos.
- Leases `nexojuris-lease/v1` canônicos e assinados com chave Ed25519
  online separada da chave principal das licenças ACT4.
- Nonces Base64URL de alta entropia, persistidos por hash para impedir replay.
- Rate limiting independente para os espaços público e administrativo.
- Transporte exclusivamente HTTPS, com TLS 1.2 mínimo e cabeçalhos de
  endurecimento.
- Tokens administrativos armazenados somente como hash e mutações registradas
  na trilha de auditoria.
- Cliente HTTPS com verificação de assinatura, `key_id`, nonce e licença antes
  da substituição atômica do lease local.
- Fallback offline que preserva o último lease válido quando o serviço está
  indisponível ou responde com conteúdo inválido.
- Integração do lease verificado com a máquina de estados ACT4 e com a ação
  manual de verificação da interface.

O contrato público aceita somente identificador da licença, identificador da
máquina, nonce e, na ativação, código de ativação. PDFs, Markdown e conteúdo de
documentos são rejeitados pelo validador estrito de campos.

## Configuração operacional

`license_online_config.py` mantém URL e chaves públicas vazias por padrão. O
serviço online só é ativado no Conversor após a configuração explícita de um
endpoint HTTPS e das chaves públicas de lease. Chaves privadas são carregadas
pelo processo do serviço a partir de arquivos criptografados; as senhas e o
token administrativo inicial entram por variáveis de ambiente específicas.

## Gate

`tests/test_phase7_license_service.py` cobre assinatura e adulteração, nonce
divergente e replay, licença inexistente, máquina divergente, revogação,
expiração, rate limiting, separação administrativa, rejeição de conteúdo
documental, cache verificado, indisponibilidade do serviço e exigência de TLS.

O commit e o push permanecem adiados para o fechamento conjunto das Fases 7,
8 e 9, conforme o ciclo de publicação definido para esta frente.
