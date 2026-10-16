# NexoJuris Conversor v1.6.3

Corrige a reconstrução editorial e a formatação dos materiais convertidos com
regras gerais baseadas no contexto e na geometria da fonte, sem exceções por apostila.

- Reconstrói títulos, questões, alternativas, gabaritos e notas de continuação,
  evitando confundir enumerações internas com novas seções ou questões.
- Recupera quadros, células e separadores a partir do PDF e recompõe linhas e
  endereços quebrados sem completar conteúdo ausente.
- Mantém âncoras e navegação consistentes entre Markdown, JSON e HTML; expõe
  diagnósticos editoriais para revisão dos casos ambíguos.
- Corrige a impressão dos seis níveis de título, reduzindo títulos isolados no
  fim da página, e inclui os novos módulos nos manifestos de distribuição.

O JSON mantém `schema_version: 2` e passa a `structure_revision: 3`.
Reconversões podem alterar IDs derivados da estrutura; consumidores que persistem
esses IDs devem reindexar os documentos reconvertidos.

A revisão dos dois materiais de referência cobriu 241 páginas impressas,
209 títulos, 49 questões e seus 49 gabaritos. O relatório consolidado registra
os casos e as verificações; esses resultados não certificam qualquer PDF futuro.
Os PDFs, HTMLs privados e os logs brutos não fazem parte da distribuição pública.

O instalador Windows inclui o runtime do conversor e não inclui o Admin nem
chaves privadas. A assinatura Authenticode permanece ausente (`NotSigned`).
O SHA-256 e o relatório público de validação acompanham os arquivos da release.

Referências: [contrato da revisão 3](CONTRATO_ESTRUTURAL_REVISAO_3.md),
[correção consolidada](RELATORIO_CORRECAO_CONSOLIDADA_5_6.md) e
[distribuição Windows](DISTRIBUICAO_WINDOWS.md).


## Distribuição e validação

[Release v1.6.3](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/tag/v1.6.3), gerada do commit
`d4fe197ad0bba59578044bcb6c659ebd95045536` da `main`.

- [Instalador Windows](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.3/NexoJuris-Conversor-Setup-v1.6.3.exe), com 139.093.703 bytes.
- [SHA-256](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.3/SHA256SUMS.txt) e [relatório público de validação](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.3/release-validation-v1.6.3.json).
- 390 testes Python aprovados, sem testes ignorados na execução local com os materiais de referência; 11 testes JavaScript, Ruff, build web e recursos web aprovados.
- [CI Linux e Windows](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/actions/runs/38922654955) aprovado no commit da release. As verificações do corpus privado não são executadas no CI público.
- Gate local do pacote aprovado, incluindo runtime, WebView2 ativo por 60 segundos, spawn, NTFS, atualização, remoção e dados de teste preservados.
- Gate local do instalador aprovado: instalação limpa, probe, registro do desinstalador, atualização, remoção e dados de teste preservados.

A assinatura Authenticode permanece `NotSigned`. A consulta ao Dependabot na
preparação encontrou quatro alertas preexistentes em `urllib3` e `multidict`
(dois altos e dois médios); esta versão não altera essas dependências nem encerra
os alertas. A geração e os testes não atualizaram a instalação de uso diário.
