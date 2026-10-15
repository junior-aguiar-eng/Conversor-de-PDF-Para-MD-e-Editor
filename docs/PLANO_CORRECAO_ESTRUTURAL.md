# Correção de estrutura documental

Base: `d7eee7b2c6e792790c139d683a829dc5f295f478` (main, v1.6.1).

1. Reproduzir a conversão original e manter a referência local, sem versionar
   o PDF privado nem seus artefatos.
2. Corrigir a seleção de perfil: opção automática conservadora e rótulos
   explícitos, preservando as escolhas manuais. Reutilizar a preparação de curso.
3. Corrigir fragmentos de estilo usando evidência da fonte e executar a junção
   entre páginas depois do rebaixamento de falsos títulos.
4. Delimitar questões e comentários pelo contexto da coleção; extrair somente
   alternativas observadas e respostas expressas, com ambiguidades registradas.
5. Publicar contrato versionado com intervalos textuais, IDs derivados do caminho
   de títulos, diagnósticos de sumário e verificações executadas/não executadas.
6. Qualificar páginas visuais e proteger imagens/OCR antes da extração. Em caso
   de conteúdo raster cuja privacidade não possa ser garantida, omitir a
   conversão visual e registrar a limitação.
7. Vincular títulos HTML por identidade verificada e detectar divergências.
8. Executar regressões, reconverter as 94 páginas, verificar invariantes,
   inspecionar páginas e executar o leitor HTML.

Escopo: conversor e seus formatos. Sem serviços externos, alterações de licença,
instalação, publicação ou funções pedagógicas. Arquivos locais de auditoria ficam
em `docs/fidelity_audit_tmp/`; entregáveis privados em `PDFs Convertidos/`.
