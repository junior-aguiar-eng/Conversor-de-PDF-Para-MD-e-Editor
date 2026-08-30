# Fase 3 — Expiração offline e proteção temporal

**Status:** concluída

**Data:** 30/08/2026

## Implementação

`trusted_time.TemporalGuard` persiste o maior horário UTC observado e o último
horário confiável recebido do servidor. O estado é serializado de forma estrita,
protegido pela DPAPI no contexto do usuário Windows e gravado atomicamente em
`license-time.dat`.

Uma segunda cópia protegida fica em
`HKCU\\Software\\NexoJuris\\Conversor\\LicenseTimeAnchorV1`. A redundância impede
que reinstalação dos arquivos do aplicativo ou restauração de um backup antigo
reduza silenciosamente o maior horário conhecido. Se as duas cópias perderem a
integridade, a validação falha fechada.

## Regras temporais

- todos os cálculos usam instantes UTC; alteração apenas do fuso não muda o
  resultado;
- retrocessos de até cinco minutos são tolerados, mas não reduzem o horário
  efetivo usado para calcular a expiração;
- retrocesso superior à tolerância produz `clock_tampered`;
- avanço do relógio atualiza a âncora e pode levar normalmente a `expiring` ou
  `expired`;
- `record_trusted_server_time()` recupera o estado somente após o chamador validar
  a assinatura de uma resposta online;
- a ausência do serviço não afeta licenças ACT4 em modo `offline`.

A proteção dificulta fraude casual, mas não promete inviolabilidade diante de um
usuário com controle administrativo total da máquina e capacidade de remover
simultaneamente todas as âncoras locais.

## Gate

Os testes cobrem avanço, retrocesso relevante, ajuste pequeno, mudança de fuso,
recuperação por horário confiável, reinstalação simulada, restauração de backup
antigo, corrupção de uma ou das duas âncoras e round-trip real da DPAPI no
Windows.

No fechamento conjunto das Fases 1 a 3, a suíte completa executou 239 testes com
resultado aprovado.
