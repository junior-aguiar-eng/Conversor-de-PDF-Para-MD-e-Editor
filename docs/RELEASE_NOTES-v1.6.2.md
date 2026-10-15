# NexoJuris Conversor v1.6.2

Corrige a organização de apostilas em Markdown, JSON e HTML e restaura o build
dos recursos web com o CLI Tailwind 4 fixado no lockfile.

- Perfil automático, títulos hierárquicos, continuidade entre páginas e separação
  de questões e gabaritos expressos, sem inferir respostas ou completar alternativas.
- Intervalos por linha/página, IDs derivados do caminho textual, diagnóstico do
  sumário e âncoras HTML verificadas. **O schema JSON passa para v2**: consumidores
  que persistem IDs v1 precisam reindexar os documentos.
- Marcas pessoais reconhecidas são protegidas antes de extrair pixels/OCR.
  Capas cujo raster não foi verificado permanecem com omissão explícita.
- Recursos web regenerados, dependência de build vulnerável substituída e build
  executado no CI Linux/Windows.
- Fixtures de OCR inicializadas e cache de testes isolado, corrigindo uma falha
  intermitente do CI sem mudar o comportamento do OCR em produção.

O instalador destina-se ao Windows x64 e inclui o runtime do conversor.
A geração desta versão não instala nem ativa o aplicativo de uso diário.
O instalador não tem assinatura Authenticode; o Windows pode exibir um aviso
de editor desconhecido. O hash SHA-256 acompanha o arquivo distribuído.

Detalhes no [contrato estrutural](CONTRATO_ESTRUTURAL_V2.md), no
[relatório da revisão](RELATORIO_CORRECAO_ESTRUTURAL.md) e no
[guia de distribuição Windows](DISTRIBUICAO_WINDOWS.md). Os PDFs e as conversões privadas não fazem
parte da release pública.

## Distribuição e validação

[Release v1.6.2](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/tag/v1.6.2),
gerada do commit `d20bcbc2086dffe45a1262fd924d3cf5dd657f17` da `main`:

- [Instalador Windows](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.2/NexoJuris-Conversor-Setup-v1.6.2.exe), com 139.070.844 bytes.
- [SHA-256](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.2/SHA256SUMS.txt) e
  [relatório público de validação](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.2/release-validation-v1.6.2.json).

338 testes Python e os seis arquivos de testes JavaScript foram aprovados,
assim como build web, Ruff e recursos web. O
[CI da `main`](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/actions/runs/38764637256)
aprovou Linux e Windows. O gate local do executável aprovou WebView2 por 60 segundos
e as verificações de runtime, NTFS, atualização, remoção e dados preservados.
O instalador passou no gate local de instalação limpa, probe, registro de
desinstalação, atualização e remoção com dados de teste preservados.

Esses resultados se referem à distribuição publicada, não a qualquer mudança
futura na `main`. A inspeção do documento convertido foi amostral e não constitui
certificação de fidelidade visual ou de correção jurídica.
