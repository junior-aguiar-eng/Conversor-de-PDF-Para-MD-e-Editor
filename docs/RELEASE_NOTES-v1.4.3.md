# NexoJuris Conversor v1.4.3

## Licenciamento

- Impede que a interface permaneça indefinidamente em “Verificando licença”.
- Adiciona timeout, novas tentativas de conexão com a ponte WebView2 e um estado
  explícito quando a verificação não pode ser concluída.
- Mantém a interface bloqueada sem licença válida e oferece somente o
  encerramento seguro do aplicativo, sem permitir continuar por trás do modal.
- Reposiciona a importação de arquivos `.nxjlic` no diálogo de licenciamento.

## Validação

- 293 testes Python e 15 testes JavaScript aprovados.
- Fluxo real WebView2 validado com bloqueio do modal e encerramento coordenado.
- Ruff, assets web, sintaxe JavaScript e integridade do diff aprovados.

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso
de origem desconhecida.
