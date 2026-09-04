# NexoJuris Conversor v1.4.6

## Licenciamento offline puro

- Remove o serviço e o cliente de ativação online, leases, suspensão,
  revogação, reativação e migração de formatos legados.
- Mantém emissão, renovação, troca de computador e reexportação de licenças
  ACT4 assinadas, com prazo de 3, 6 ou 12 meses.
- Preserva o mesmo `license_id` e o vencimento vigente na troca de computador,
  registrando cada revisão no histórico administrativo.
- Simplifica o Admin para as operações essenciais e estados derivados do prazo.

## Confiabilidade

- Torna a escrita dos checkpoints resiliente a bloqueios transitórios do
  Windows e do OneDrive.
- Corrige a verificação de espaço quando a pasta de saída ainda não existe.
- Estabiliza o gate do corpus OCR diante de uma substituição isolada típica do
  reconhecimento óptico, sem flexibilizar a ordem das âncoras.

## Validação

- 263 testes Python aprovados.
- Ruff e verificação de integridade do diff aprovados.

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso
de origem desconhecida.
