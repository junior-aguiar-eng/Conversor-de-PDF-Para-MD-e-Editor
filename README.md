# Boni - Conversor de PDF para Markdown

Aplicativo de uso manual para converter PDFs em arquivos `.md` localmente. Não monitora diretórios, não agenda tarefas e não altera os PDFs originais.

## Instalação no disco D

1. Tenha o [uv](https://docs.astral.sh/uv/) e o Python 3.13 disponíveis. Se necessário, execute `uv python install 3.13`.
2. Abra o PowerShell na pasta deste projeto e execute:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\instalar_no_d.ps1
   ```

   Para usar outra pasta no D, informe-a: `.\instalar_no_d.ps1 -Destino "D:\Meus Programas\PDF para Markdown"`.
3. Abra `D:\PDF para Markdown\iniciar.vbs`. Ele inicia somente a interface gráfica, sem janela de Prompt de Comando.

O instalador cria um ambiente Python 3.13 isolado em `D:\PDF para Markdown\.venv` e instala dependências com versões travadas a partir de `requirements.lock.txt`. Não há download de modelos de IA, Docker, PyTorch ou uso de GPU.

Para reconstruir o ambiente e remover dependências antigas, com o aplicativo fechado, execute `.\instalar_no_d.ps1 -RecriarAmbiente`.

## Uso

Ao abrir o aplicativo, ele valida rapidamente se o ambiente foi instalado corretamente. Se faltar a dependência principal, a interface informa para executar `instalar_no_d.ps1`.

Selecione um ou mais PDFs digitais com texto selecionável, escolha a pasta de saída e clique em **Converter para Markdown**. O aplicativo usa PyMuPDF4LLM sem OCR, sem modelos de IA, GPU, PyTorch ou Docker. PDFs escaneados, fórmulas e layouts muito complexos não são o objetivo deste conversor leve.

Cada conversão gera um `.md`. As imagens ficam em `images/Nome-do-PDF/`, com links relativos que funcionam no Obsidian e em leitores Markdown comuns. A opção de partes cria `Nome-do-PDF_partes/parte_001.md`, respeitando títulos `#` e `##` quando o arquivo ultrapassa o limite escolhido. **Pausar** suspende a fila antes do próximo PDF e **Parar** encerra a fila depois de concluir o PDF em andamento, preservando os resultados já produzidos.

Após a conversão, selecione um PDF na lista e clique em **Abrir Markdown selecionado** para abrir o resultado diretamente no aplicativo padrão do Windows.

PyMuPDF4LLM é distribuído sob AGPL ou licença comercial. Verifique a compatibilidade da licença antes de redistribuir o aplicativo.
