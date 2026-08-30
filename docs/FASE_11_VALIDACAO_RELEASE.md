# Fase 11 — Validação e release

Execução em 30 de agosto de 2026 sobre a versão `1.4.0`.

## Matriz obrigatória

| Cenário | Evidência | Resultado |
|---|---|---|
| Instalação limpa | Gate real do instalador Inno | Aprovado |
| Atualização da versão atual | Gate do executável e reinstalação Inno no mesmo destino | Aprovado |
| ACT3 existente | Teste de atualização sem dependência do serviço e migração F9 | Aprovado |
| Nova licença ACT4 | Roundtrip criptográfico, emissão Admin e ativação | Aprovado |
| Licença válida offline | Estados do cliente e execução sem cliente online configurado | Aprovado |
| Avisos de expiração | Marcos de 30, 15, 7, 3 e 1 dia | Aprovado |
| Expiração efetiva | Limites temporais e bloqueio por licença expirada | Aprovado |
| Renovação online | Fluxo ponta a ponta Admin, API, lease e cliente | Aprovado |
| Renovação por arquivo | Substituição do ACT4 somente após validação | Aprovado |
| Revogação | API e estado local revogado | Aprovado |
| Suspensão | API e estado local suspenso | Aprovado |
| Servidor indisponível | Cache preservado e erro de indisponibilidade controlado | Aprovado |
| Internet bloqueada | Falha de conexão simulada sem perda do cache válido | Aprovado |
| Lease vencido | Estado `online_check_required` após a tolerância | Aprovado |
| Relógio retrocedido | Proteção temporal persistente | Aprovado |
| Troca de computador | Transferência administrativa e rejeição da máquina anterior | Aprovado |
| Reinstalação | Dados externos à pasta instalada preservados no gate | Aprovado |
| Desinstalação com preservação | Desinstalação real em diretório temporário e sentinel externo preservado | Aprovado |
| Build e instalador Windows | PyInstaller 6.22.2 e Inno Setup 6.7.3 | Aprovado |

## Resultados técnicos

- Python: 283 testes aprovados.
- JavaScript: 15 testes aprovados.
- Ruff: aprovado sobre o repositório, excluídos apenas os artefatos gerados em `release`.
- Gate do executável: runtime, WebView2, multiprocessing spawn, soak de 60 segundos, atualização e desinstalação simuladas aprovados.
- Gate do instalador: instalação, atualização e desinstalação silenciosas reais aprovadas; registro do desinstalador removido e dados preservados.
- Memória máxima observada no soak: 233.009.152 bytes.
- Assinatura Authenticode: ausente.

## Limites de publicação

Os artefatos foram gerados localmente em `release/packages`. Não houve publicação em GitHub Release, upload externo, deploy do serviço nem provisionamento de chaves de produção.
