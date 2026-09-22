# NexoJuris Conversor v1.4.8

## Hardening Anti-Engenharia Reversa e Proteção do Código

- Compilação dos módulos proprietários do backend em extensões binárias nativas C (`.pyd`) via Cython e MSVC.
- Minificação e ofuscação das fontes frontend (`app.js`) com Terser no pipeline de build e empacotamento.
- Arquivos de código-fonte intermediários e descompiláveis removidos dos pacotes de distribuição.

## Instalador e Experiência do Usuário

- Atualização do instalador Inno Setup (`installer/NexoJuris-Conversor.iss`) para ocultar a listagem dos nomes de arquivos descompactados durante a instalação, mantendo interface limpa com foco na barra de progresso.

## Licenciamento ACT4 e Resiliência

- Adição de contingência por Serial de Volume do sistema operacional no hardware locking para garantir integridade e estabilidade em máquinas virtuais ou ambientes com restrições severas ao Registro do Windows.
- Criação e disponibilização da ferramenta administrativa `scripts/emitir_licenca.py` para geração e assinatura rápida de arquivos `.nxjlic`.
- Ajuste de timeout defensivo de ativação para 25s com retry gracioso no frontend.

## Estabilidade Operacional e Dependências

- Saneamento de dependências não utilizadas (`pdfplumber`, `pypdf`, `pytesseract`) e fixação de concorrência com `ThreadPoolExecutor` para mitigar vazamentos de threads.
