# NexoJuris Conversor v1.4.5

## Correções críticas de licenciamento

- Exige licença ACT4 no cliente; chaves ACT2/ACT3 ficam restritas à migração
  administrativa e não liberam Conversor, Leitor ou OCR.
- Mantém o painel de ativação bloqueante enquanto não houver um arquivo
  `.nxjlic` ACT4 válido.
- Remove do cliente a entrada de chaves legadas, preservando apenas a importação
  do arquivo ACT4.

## Admin

- Permite selecionar a chave privada criptografada diretamente pela interface.
- Valida a senha, o formato Ed25519 e a correspondência com a chave pública do
  Conversor antes de instalar a chave no perfil administrativo.
- Inicia o Admin mesmo quando a chave ainda não foi configurada e impede a
  exportação até a configuração válida.

## Validação

- 301 testes Python aprovados com perfil local vazio.
- 20 testes JavaScript e Ruff aprovados.

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso
de origem desconhecida.
