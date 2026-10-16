# Distribuição Windows — v1.6.3

## Download e proveniência

[Baixar o instalador Windows](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.3/NexoJuris-Conversor-Setup-v1.6.3.exe).
Para uso normal, basta esse `.exe`; não é necessário baixar o Admin ou instalar
Python/Node.js. A interface usa Microsoft Edge WebView2 disponível no Windows.

| Campo | Valor |
|---|---|
| Release | [v1.6.3](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/tag/v1.6.3) |
| Commit de origem na `main` | `d4fe197ad0bba59578044bcb6c659ebd95045536` |
| Instalador | `NexoJuris-Conversor-Setup-v1.6.3.exe` |
| Tamanho | 139.093.703 bytes |
| SHA-256 | `7e42100ceaffb644dd8c46b1be29319e760dfe825cbf9656f50fa06443c67a37` |
| Authenticode | `NotSigned` |

Anexos públicos:
[SHA256SUMS.txt](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.3/SHA256SUMS.txt) e
[release-validation-v1.6.3.json](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/releases/download/v1.6.3/release-validation-v1.6.3.json).
O digest informado pelo GitHub foi comparado com o hash do instalador local.
A tag permanece vinculada ao commit acima; um commit documental posterior não
recompila nem substitui esse arquivo.

Para conferir um download no PowerShell:

```powershell
Get-FileHash -LiteralPath '.\NexoJuris-Conversor-Setup-v1.6.3.exe' -Algorithm SHA256
```

O instalador não tem assinatura digital. O Windows pode mostrar um aviso de
editor desconhecido; isso não substitui a verificação de origem e hash.

## Instalação e dados do usuário

O Inno Setup usa instalação por usuário (`PrivilegesRequired=lowest`), arquitetura
`x64compatible` e destino padrão `%LOCALAPPDATA%\Programs\NexoJuris Conversor`.
Ele inclui a pasta completa da distribuição PyInstaller, inclusive `_internal`;
copiar apenas o executável dessa pasta não equivale a instalar o produto.

A ativação continua offline por arquivo ACT4 `.nxjlic`. O instalador não emite
licenças nem instala a chave privada do Admin. Dados graváveis ficam separados
da pasta do programa, sob `%LOCALAPPDATA%\NexoJuris\Conversor`; o acervo e outros
dados de aplicação ficam em `data/`, o cache OCR em `ocr_cache/` e logs em
`diagnostics/`. O modo de testes pode isolar esse perfil por `NEXOJURIS_DATA_DIR`.

Os gates da release instalaram cópias em diretórios temporários. A instalação de
uso diário e os documentos reais do usuário não foram atualizados pelos testes.

## Reproduzir o build

Em um checkout limpo da tag `v1.6.3`, use Windows com Python 3.14, uv, Node.js/npm,
WebView2 e Inno Setup 6 disponíveis. A etapa opcional Cython usa um compilador C
compatível; a build local publicada conseguiu compilar os módulos nativos.

```powershell
uv sync --frozen --group dev
npm ci
npm run build:web
uv run --frozen --group dev python build_app.py
```

`build_app.py` chama a implementação em `build_support.py`, também usada pelo
wrapper `build_release.ps1`. O wrapper encerra processos do Conversor/Admin por
nome; na geração publicada foi usada a chamada Python direta para preservar
processos de uso diário. O build recria `release/dist/` e `release/build/`;
preserve artefatos necessários antes de recompilar.

Saídas separadas:

- `release/dist/NexoJuris Conversor/`: cliente público.
- `release/dist/NexoJuris Licenças Admin/`: distribuição administrativa privada.

Para gerar o instalador, ajuste o caminho do compilador Inno Setup instalado:

```powershell
$isccPath = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
& $isccPath installer/NexoJuris-Conversor.iss
if ($LASTEXITCODE -ne 0) { throw 'Falha na compilação do instalador.' }
```

Saída: `release/packages/NexoJuris-Conversor-Setup-v1.6.3.exe`.
O manifesto do cliente inclui somente sua distribuição; o Admin não faz parte
desse instalador. Não inclua chaves privadas, PDFs, conversões ou logs brutos
como anexos públicos.

## Gates e resultados confirmados

Execute os gates em um perfil de teste, com o build correspondente:

```powershell
.\scripts\windows_release_gate.ps1 `
  -ReleaseRoot 'release/dist/NexoJuris Conversor' `
  -ReportPath 'release/windows-release-gate-v1.6.3.json' `
  -SoakSeconds 60

.\scripts\windows_installer_gate.ps1 `
  -Installer 'release/packages/NexoJuris-Conversor-Setup-v1.6.3.exe' `
  -ReportPath 'release/windows-installer-gate-v1.6.3.json'
```

O gate do instalador interrompe a execução se já houver uma instalação com seu
AppId registrada no perfil. Ele desabilita atalhos durante o teste e valida o
registro temporário do desinstalador; não remova a instalação diária para fazê-lo
passar. Use um perfil ou máquina de teste sem esse registro.

| Evidência da v1.6.3 | Resultado confirmado |
|---|---|
| Suíte Python | 390 testes aprovados na execução local com o corpus privado |
| JavaScript | 11 testes aprovados |
| Build web, Ruff, recursos web | Aprovados |
| Auditoria npm local na preparação | Zero vulnerabilidades reportadas naquela execução |
| [CI da `main`](https://github.com/junior-aguiar-eng/Conversor-de-PDF-Para-MD-e-Editor/actions/runs/38922654955) | Linux e Windows aprovados no commit da release |
| Gate do pacote PyInstaller | Runtime, importação WebView2, spawn, interface ativa por 60 s, NTFS, atualização, remoção e dados preservados |
| Gate do instalador Inno Setup | Instalação limpa, probe, registro do desinstalador, atualização, remoção e dados preservados |

O CI compila e testa a distribuição PyInstaller. A compilação Inno Setup e o gate
do instalador foram executados localmente; não atribua essa etapa ao CI. O relatório
público resume ambos os gates sem publicar caminhos pessoais dos logs locais.

O aviso do GitHub sobre Dependabot da branch padrão é uma fonte distinta do
`npm audit` local e não deve ser declarado encerrado apenas com esse resultado.
Os gates não comprovam assinatura Authenticode, compatibilidade com toda máquina
Windows ou qualidade jurídica de toda conversão. A inspeção das 241 páginas dos dois materiais de referência está
no [relatório consolidado](RELATORIO_CORRECAO_CONSOLIDADA_5_6.md).

Na preparação da v1.6.3, o Dependabot manteve quatro alertas preexistentes em
`urllib3` e `multidict` (dois altos e dois médios). As dependências não foram
alteradas nesta correção editorial. O CI público não executa o corpus privado.
