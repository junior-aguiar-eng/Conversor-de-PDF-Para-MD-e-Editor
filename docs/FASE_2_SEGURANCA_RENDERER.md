# Fase 2 — Segurança do renderer sem empobrecimento da interface

Status: implementada e validada por testes automatizados.

## Renderer Markdown

- `marked` faz o parsing GFM; `DOMPurify` aplica allowlist estrita.
- Permanecem títulos, listas, tabelas, citações, blocos de código, formatação inline, imagens relativas e links HTTP/HTTPS.
- Scripts, HTML ativo, eventos, atributos arbitrários, esquemas inseguros, credenciais em URL e imagens externas são removidos.
- Imagens locais são lidas pelo backend somente dentro da pasta do Markdown autorizado e devolvidas como `data:`.
- Links externos são validados pelo backend e abertos no navegador padrão.

## Interface autocontida e CSP

- Tailwind é compilado no build; fontes, CSS, `marked` e `DOMPurify` são empacotados localmente.
- Handlers HTML inline foram substituídos por eventos registrados com `addEventListener` e despacho declarativo por allowlist.
- CSP restringe scripts, conexões, objetos, formulários, frames e origens de mídia.
- O build falha se faltar asset local, reaparecer dependência remota ou handler HTML inline.

## Autorização de arquivos

- Caminhos privilegiados foram substituídos por identificadores opacos e imprevisíveis.
- O registro conserva origem, tipo e capacidades do recurso e cobre diálogo nativo, drag-and-drop confirmado por diálogo nativo, CLI/“Enviar para”, acervo, conversão, pasta de saída e documentos recentes.
- Reabertura do acervo e dos recentes reautoriza arquivos persistidos existentes; o banco não é limitado à duração da sessão.
- Assets relativos passam por canonicalização e contenção; travessia codificada, caminho absoluto, UNC, esquema, query, fragmento e escape por symlink são recusados.
- A hidratação limita-se a 200 imagens por preview, com oito leituras simultâneas e teto de 20 MB por asset.

## Gates

- Testes Python do projeto.
- Testes Node de integridade e segurança do renderer.
- Verificação de assets/CSP e sintaxe.
- Auditoria de dependências npm.
- Build empacotado e smoke test de inicialização.

A comparação visual automática por screenshot pode depender do ambiente gráfico: a validação funcional automatizada garante que os controles declarativos continuam resolvíveis, mas não substitui inspeção humana de diferenças de rasterização de fontes.
