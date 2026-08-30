# NexoJuris Conversor v1.4.0

## Licenciamento

- Protocolo ACT4 assinado, vinculado ao computador e com prazos comerciais de 3, 6 e 12 meses.
- Compatibilidade preservada com ativações ACT3 existentes.
- Modos offline e híbrido, lease temporário, tolerância à indisponibilidade e proteção contra retrocesso do relógio.
- Avisos progressivos de expiração e bloqueio efetivo após o término da licença ou do prazo offline.
- Renovação online ou por novo arquivo ACT4, suspensão, reativação, revogação e transferência administrativa.
- Migração explícita de ACT2/ACT3 para ACT4 com histórico e rollback controlado no cliente.

## Administração e segurança

- Banco administrativo local, pesquisa, histórico, exportação e operações críticas confirmadas.
- Serviço HTTPS com autenticação administrativa, limites de requisição e respostas assinadas.
- Chaves privadas criptografadas, rotação por `key_id`, backup offline e separação entre teste e produção.
- Senha administrativa com Scrypt, token revogável, TOTP remoto e auditoria encadeada.

## Validação

- 283 testes Python e 15 testes JavaScript aprovados.
- Build oficial com Python 3.14.6 e PyInstaller 6.22.2.
- Smoke/soak WebView2 de 60 segundos, instalação limpa, atualização simulada e preservação de dados aprovados.
- Instalador Inno Setup 6.7.3 aprovado em instalação limpa, atualização e desinstalação reais em diretório temporário.
- Artefatos inspecionados sem banco, licença, backup ou chave privada; `cacert.pem` é apenas o repositório público de autoridades certificadoras do `certifi`.

## Artefatos

- `NexoJuris-Conversor-Setup-v1.4.0.exe`
  - SHA-256: `394b1a1d6ef7cfce1ac38ecb63e24828f9599d0db360af27b54f6a1d2a76f88f`
- `NexoJuris-Conversor-v1.4.0-windows-x64.zip`
  - SHA-256: `2b5909b2cb123a75fd75dcaef901dbdcf678227ea8f154ea51bd9bdbb8e64a3f`

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso de origem desconhecida.
