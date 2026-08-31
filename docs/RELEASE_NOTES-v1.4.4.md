# NexoJuris Conversor v1.4.4

## Licenciamento e Admin

- Restringe o Admin local à emissão de licenças offline.
- Impede a exportação de licenças expiradas.
- Torna a migração legada atômica, sem criar clientes ou licenças órfãos em
  tentativas repetidas.
- Bloqueia cliques e submissões duplicados durante operações administrativas.

## Acervo

- Isola o banco usado pelos testes para impedir acesso ao acervo real.
- Distingue corrupção confirmada de erros transitórios de acesso ao SQLite,
  evitando quarentena indevida de bancos saudáveis.

## Distribuição administrativa

- Adiciona executável autocontido e instalador privado do Admin, sem incorporar
  a chave privada criptografada.
- Mantém a chave e o banco administrativo no perfil local do operador.

## Validação

- 298 testes Python e 18 testes JavaScript aprovados.
- Ruff, sintaxe JavaScript e PowerShell e integridade do diff aprovados.
- Builds PyInstaller do Conversor e do Admin aprovados.
- Probe do Admin confirmou interface, SQLite, criptografia e `pywebview`.

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso
de origem desconhecida.
