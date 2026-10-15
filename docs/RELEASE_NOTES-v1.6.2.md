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

Detalhes do contrato em `CONTRATO_ESTRUTURAL_V2.md` e da revisão em
`RELATORIO_CORRECAO_ESTRUTURAL.md`. Os PDFs e as conversões privadas não fazem
parte da release pública.
