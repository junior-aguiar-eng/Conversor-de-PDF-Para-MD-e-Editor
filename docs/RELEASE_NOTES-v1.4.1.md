# NexoJuris Conversor v1.4.1

## Correção

- Impede a abertura simultânea de duas interfaces gráficas na mesma sessão do Windows.
- Mantém disponíveis as conversões rápidas iniciadas por arquivo ou linha de comando.
- Libera o controle de instância também quando a inicialização da interface falha.

## Validação

- 289 testes Python e 15 testes JavaScript aprovados.
- Testes específicos cobrem criação, detecção, falha e liberação do mutex.
- Ruff aprovado sobre o repositório, excluídos apenas os artefatos gerados em `release`.

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso de origem desconhecida.
