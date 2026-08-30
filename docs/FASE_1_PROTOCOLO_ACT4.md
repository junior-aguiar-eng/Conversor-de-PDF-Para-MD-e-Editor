# Fase 1 — Protocolo criptográfico ACT4

**Status:** concluída

**Data:** 30/08/2026

## Formato

O arquivo `.nxjlic` contém um objeto JSON UTF-8 com exatamente três campos:

```json
{
  "format": "nexojuris-license-file/v1",
  "payload": {
    "schema": "nexojuris-license/v4",
    "key_id": "license-main-2026-01",
    "license_id": "LIC-2026-000001",
    "machine_id": "NXJ2-1111-2222-3333-4444",
    "issued_at": "2026-09-01T00:00:00Z",
    "not_before": "2026-09-01T00:00:00Z",
    "expires_at": "2027-09-01T00:00:00Z",
    "validation_mode": "offline",
    "max_offline_days": 0,
    "features": ["converter", "ocr", "reader"],
    "customer_reference": "CLI-000001"
  },
  "signature": "BASE64URL_SEM_PADDING"
}
```

`validation_mode` materializa a decisão da Fase 0: licenças `offline` usam
`max_offline_days: 0`; licenças `hybrid` aceitam de 1 a 30 dias. O padrão
comercial híbrido será 7 dias, definido pelo emissor administrativo.

## Assinatura e canonicalização

A assinatura Ed25519 cobre exclusivamente a serialização canônica integral de
`payload`: chaves em ordem lexicográfica, UTF-8, sem espaços supérfluos, sem NaN
e sem campos opcionais. Os tipos e textos aceitos pelo schema são restritos, de
modo que essa regra produz uma única representação por payload válido.

O envelope completo também é emitido deterministicamente, com uma quebra de
linha final. A assinatura usa Base64URL sem padding. Campos ausentes, extras ou
duplicados são rejeitados.

## Chaves

`key_id` faz parte do payload assinado e seleciona a chave no `PublicKeyRing`.
A rotação adiciona uma nova chave pública ao chaveiro sem remover as anteriores
que ainda assinem licenças válidas. Um `key_id` desconhecido é rejeitado antes de
qualquer autorização.

Licenças completas aceitam somente identificadores iniciados por `license-`.
Chaves online usarão o prefixo reservado `lease-` e não podem assinar um payload
`nexojuris-license/v4`, mesmo que sejam inseridas por engano no mesmo chaveiro.

O núcleo recebe objetos `Ed25519PrivateKey` apenas no ato de emissão; não conhece
caminhos, senhas ou armazenamento de chaves privadas. O Conversor utilizará
somente `PublicKeyRing`.

## Compatibilidade

`ACT2` e `ACT3` continuam validadas exclusivamente pelo módulo legado
`licensing.py`. `license_core.legacy` apenas classifica tokens para permitir o
roteamento explícito; o parser ACT4 nunca aceita um token legado como `.nxjlic`.

## Limites da fase

Esta fase entrega protocolo, assinatura, verificação e validações criptográficas
básicas. Persistência no Conversor, máquina de estados, bloqueios de features,
proteção temporal local e interface pertencem às Fases 2 a 4.

## Gate

O gate cobre serialização canônica, emissão e verificação Ed25519, adulteração de
payload e assinatura, divergência de máquina, limites temporais, formatos e
schemas desconhecidos, rotação e finalidade de chaves, modos `offline` e
`hybrid`, extensão e tamanho de `.nxjlic`, além do isolamento de `ACT2` e `ACT3`.

Validação executada em 30/08/2026:

- 19 testes específicos de `license_core`: aprovados;
- 12 testes do licenciamento legado: aprovados;
- suíte completa: 216 testes aprovados;
- Ruff no repositório: aprovado;
- compilação sintática dos novos módulos: aprovada;
- `git diff --check`: aprovado para os arquivos rastreados.
