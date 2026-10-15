# NexoJuris Conversor v1.6.1

Corrige a inclusão de marcas textuais de identificação pessoal do PDF nos arquivos
convertidos, especialmente quando a extração principal precisa recorrer ao texto
simples. A proteção atua nos dois perfis de títulos.

- Reconhece campos pessoais nas margens pela geometria, repetição e agrupamento
  de identificação, incluindo vários campos na mesma linha.
- Remove os identificadores reconhecidos antes dos checkpoints e da geração do
  Markdown, das partes, do JSON e do conteúdo incorporado ao HTML.
- Protege também avisos de falha e textos com estilos, entidades e marcação HTML.
- Recalcula checkpoints anteriores à política de proteção. Exportações antigas
  precisam ser convertidas novamente; instalar esta versão não altera esses arquivos.
- Mantém as estatísticas de proteção sem registrar os valores pessoais no JSON.
- Preserva o PDF original e os nomes e números distintos presentes nos exemplos
  jurídicos. Uma falha na inspeção textual interrompe a conversão com erro genérico.

## Validação

326 testes Python e 11 testes JavaScript, Ruff e verificação dos recursos web.
Na apostila digital de 94 páginas usada na reprodução, o fluxo completo manteve
143 títulos, três tabelas e seis partes Markdown, com 92 extrações principais e
duas alternativas. Não foram encontrados os identificadores conhecidos nos arquivos
finais. Duas conversões produziram Markdown idêntico; os originais foram preservados.
O código recebeu revisão independente com dados sintéticos.

O instalador acompanha relatório de validação do pacote Windows e hashes SHA-256.
Os testes de instalação e atualização usam diretórios temporários, preservando
os dados do perfil. A instalação de uso diário não é atualizada por esses testes.

## Limites

É proteção de marcas textuais identificadas, sem garantia de anonimização geral
de documentos ou de informações existentes apenas em imagens/digitalizações.
Os avisos de fidelidade continuam disponíveis para conferência editorial.
Utilize uma nova conversão ao compartilhar materiais anteriormente exportados.

O instalador não tem assinatura Authenticode. A compilação opcional Cython pode
recorrer à alternativa Python/PyInstaller prevista no projeto; o relatório da
release registra o resultado efetivo do empacotamento.
