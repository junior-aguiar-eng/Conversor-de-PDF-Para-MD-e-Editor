# NexoJuris Conversor v1.4.7

## Correção do Admin

- Corrige a primeira abertura após atualização de um banco administrativo do
  modelo anterior.
- Preserva o banco antigo em arquivo `pre-offline-v6` antes de inicializar o
  banco do licenciamento offline puro.
- A senha informada na primeira abertura do banco novo passa a proteger a nova
  conta administrativa; nenhuma senha anterior é alterada.

## Validação

- Migração exercitada sobre uma cópia do banco local schema 5.
- Suítes Python e JavaScript, Ruff e gate Windows executados antes da
  publicação.

Os artefatos não possuem assinatura Authenticode. O Windows pode exibir aviso
de origem desconhecida.
