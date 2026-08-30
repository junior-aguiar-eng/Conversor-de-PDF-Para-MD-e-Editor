# Fase 2 — Máquina de estados da licença

**Status:** concluída

**Data:** 30/08/2026

## Implementação

`license_core.state` define os estados `unlicensed`, `valid`, `expiring`,
`online_check_required`, `expired`, `revoked`, `suspended`, `clock_tampered`,
`machine_mismatch` e `invalid`. Toda avaliação produz `LicenseStatus`, com
autorização, identificadores, validade, prazo restante, janela offline, features
e mensagem destinada à interface.

Cada estado impeditivo possui exceção específica. Uma licença válida também é
recusada por `FeatureNotLicensedError` quando não contém a feature solicitada.
`require_software_activation()` foi preservada como API compatível e agora aceita
a feature opcional `converter`, `ocr` ou `reader`.

## Persistência e compatibilidade

`system_license` recebe, por migração aditiva, `license_format` e
`license_document`. As colunas e linhas ACT2/ACT3 anteriores são preservadas. O
backup `license.sig` passa a usar envelope JSON versionado, mas a leitura do
formato legado `machine_id:activation_key` continua disponível.

ACT4 é armazenada integralmente como documento assinado. ACT2 e ACT3 continuam
no verificador legado e recebem acesso às features já existentes, sem prazo ou
alteração retroativa.

## Pontos de bloqueio

- conversão normal, rápida e retomada exigem `converter` antes de criar saídas ou
  iniciar workers;
- toda execução do motor OCR exige `ocr` antes de carregar ou chamar o engine;
- abertura e renderização no leitor exigem `reader`;
- rotação, proteção, desproteção, anotações e restauração de backup exigem
  `reader` antes de resolver o arquivo para escrita ou cancelar indexação.

O bridge mantém `is_activated` para a interface atual e acrescenta o resultado
completo. O código de erro legado `license_required` permanece para instalações
sem licença, acompanhado de `license_state: unlicensed`.

## Gate

Os testes cobrem todos os estados, exceções, autorização por feature, migração de
banco, recuperação pelo backup ACT4, preservação legada e bloqueio anterior aos
efeitos colaterais. A integração com leases reais permanece reservada às Fases 7
e 8; os estados online já são determinísticos e testáveis.
