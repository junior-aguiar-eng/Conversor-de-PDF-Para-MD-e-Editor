# Documentação do NexoJuris Conversor

Estado publicado: **v1.6.2**, de 15/10/2026, a partir do commit
`d20bcbc2086dffe45a1262fd924d3cf5dd657f17` da `main`.
A documentação pode receber correções posteriores sem substituir os binários
ou mover a tag da release.

## Referências atuais

| Documento | Conteúdo |
|---|---|
| [README do projeto](../README.md) | Instalação, recursos, uso e desenvolvimento |
| [Distribuição Windows](DISTRIBUICAO_WINDOWS.md) | Instalador, SHA-256, reprodução do build, gates e evidências |
| [Notas da v1.6.2](RELEASE_NOTES-v1.6.2.md) | Alterações e limites da versão publicada |
| [Contrato estrutural v2](CONTRATO_ESTRUTURAL_V2.md) | Perfis, IDs, intervalos, questões, sumário, diagnósticos e privacidade |
| [Arquitetura e telemetria](ARQUITETURA_E_TELEMETRIA.md) | Fluxo atual, processos por documento, OCR, armazenamento e métricas |
| [Relatório da correção estrutural](RELATORIO_CORRECAO_ESTRUTURAL.md) | Causas, antes/depois, reconversão e validações |
| [Plano da correção](PLANO_CORRECAO_ESTRUTURAL.md) | Escopo original e referência ao fechamento |

## Como interpretar evidências

Testes sintéticos, CI, gate do executável, gate do instalador e inspeção de um
documento são evidências diferentes. Os resultados da v1.6.2 se referem ao commit
publicado; não certificam mudanças futuras na `main`, todos os PDFs possíveis ou
o ambiente de cada usuário.

O score lexical não certifica redação jurídica nem fidelidade visual. Páginas
visuais omitidas por privacidade e ambiguidades do parser exigem conferência.
PDFs e conversões privados não integram o repositório nem os anexos da release.

## Histórico

`RELEASE_NOTES-v*.md` anteriores à v1.6.2 descrevem suas respectivas versões.
Números de testes, limitações e estratégias registrados neles não representam
automaticamente a versão atual.

O [contrato da baseline funcional](CONTRATO_PRESERVACAO_FUNCIONAL.md), os arquivos
`FASE_*.md` e `superpowers/specs/` e `superpowers/plans/` registram etapas e decisões
anteriores. Suas matrizes iniciais e itens pendentes devem ser lidos no contexto
da fase, sem reabrir gates já concluídos. Para operação atual, use as referências
acima e o código da versão em questão.
