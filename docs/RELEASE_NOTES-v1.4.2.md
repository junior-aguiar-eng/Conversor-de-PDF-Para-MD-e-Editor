# NexoJuris Conversor v1.4.2

## Administração local

- Adiciona o perfil operacional `local` ao painel de licenças, separado dos
  ambientes `test` e `production`.
- Inclui launcher que solicita a senha administrativa sem persistir o segredo.
- Mantém o ambiente de produção condicionado ao chaveiro criptografado.

## Correção de testes

- Isola a âncora temporal da migração ACT3 para ACT4, impedindo que a suíte
  altere o Registro real do usuário.

## Validação

- 290 testes Python e 15 testes JavaScript aprovados.
- Ruff, assets web, sintaxe JavaScript e integridade do diff aprovados.

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso
de origem desconhecida.
