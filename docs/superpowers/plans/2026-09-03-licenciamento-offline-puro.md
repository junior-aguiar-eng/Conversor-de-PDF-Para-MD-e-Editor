# Licenciamento offline puro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar ativação ACT4 exclusivamente offline e um Admin orientado a emissão, renovação, troca de computador e reexportação.

**Architecture:** O `.nxjlic` é uma revisão imutável assinada de uma licença permanente. O cliente valida tudo localmente; o Admin persiste a licença atual e o histórico de revisões, sem serviço, lease ou estado remoto.

**Tech Stack:** Python 3.14, SQLite, `cryptography`/Ed25519, pywebview, HTML/CSS/JavaScript, `unittest`, Node test runner e Ruff.

**Spec:** `docs/superpowers/specs/2026-09-03-licenciamento-offline-puro-design.md`

## Global Constraints

- Prazos permitidos: 3, 6 ou 12 meses.
- Uma licença mantém o mesmo `license_id` em renovações e trocas de computador.
- O Conversor nunca recebe chave privada nem depende de rede.
- Dados do usuário permanecem acessíveis quando a licença expira.
- Não preservar ACT2, ACT3, híbrido, leases ou bancos administrativos antigos.
- Não executar commit, build, instalação, operação de chaves ou publicação.

---

### Task 1: Protocolo e estados ACT4 offline

**Files:**
- Modify: `license_core/protocol.py`
- Modify: `license_core/state.py`
- Modify: `license_core/errors.py`
- Modify: `license_core/__init__.py`
- Test: `tests/test_license_core.py`
- Test: `tests/test_license_states.py`

**Interfaces:**
- Produces: `LicensePayload(..., revision: int, ...)`, `verify_license(...)` e `evaluate_act4(...)` sem parâmetros online.

- [ ] Escrever testes que exijam `revision`, rejeitem campos híbridos e preservem validade, expiração, máquina e recursos.
- [ ] Executar os testes focais e confirmar falha pelo contrato antigo.
- [ ] Reduzir o parser/envelope e a máquina de estados ao contrato offline.
- [ ] Executar os testes focais até aprovação.

### Task 2: Runtime do Conversor sem rede nem legado

**Files:**
- Modify: `licensing.py`
- Modify: `app_storage.py`
- Modify: `web_api.py`
- Modify: `web/app.js`
- Modify: `web/index.html`
- Delete: `online_license_client.py`
- Delete: `license_online_config.py`
- Delete: `admin_keygen.py`
- Delete: `license_core/legacy.py`
- Test: `tests/test_licensing.py`
- Test: `tests/test_phase4_license_interface.py`
- Test: `tests/phase4_license_ui.test.js`

**Interfaces:**
- Consumes: `evaluate_act4(payload, machine_id, at, clock_tampered)`.
- Produces: `activate_license_document(document)` com bloqueio de revisão inferior e `get_license_status()` inteiramente local.

- [ ] Escrever testes de importação, anti-rollback e ausência de atualização online.
- [ ] Executar os testes focais e confirmar falhas.
- [ ] Remover armazenamento/cliente online, ACT2/ACT3 e estados remotos dos fluxos Python e JavaScript.
- [ ] Executar testes Python e JavaScript do cliente.

### Task 3: Banco e serviço administrativo por revisões

**Files:**
- Modify: `admin_licensing/database.py`
- Modify: `admin_licensing/service.py`
- Modify: `admin_licensing/__init__.py`
- Modify: `admin_license_bridge.py`
- Test: `tests/test_phase5_admin_licensing.py`
- Test: `tests/test_phase6_admin_search.py`
- Test: `tests/test_phase8_license_lifecycle.py`

**Interfaces:**
- Produces: `issue_license(...)`, `renew_license(...)`, `replace_device(...)`, `export_license(...)`, `search_licenses(...)` e `license_detail(...)` sobre `license_revisions`.

- [ ] Reescrever testes administrativos para revisão 1, renovação, troca com mesmo `license_id`, reexportação idêntica e estados derivados.
- [ ] Executar os testes focais e confirmar falhas.
- [ ] Substituir tabelas e operações obsoletas pelo modelo de revisões imutáveis.
- [ ] Atualizar bridge e consultas do dashboard.
- [ ] Executar todos os testes administrativos.

### Task 4: Admin visual orientado a tarefas

**Files:**
- Modify: `admin_web/index.html`
- Modify: `admin_web/app.js`
- Modify: `admin_web/style.css`
- Test: `tests/phase6_admin_ui.test.js`
- Test: `tests/phase8_admin_lifecycle.test.js`
- Delete: `tests/phase9_migration_ui.test.js`

**Interfaces:**
- Consumes: bridge administrativo da Task 3.
- Produces: central com emissão, renovação, troca, reexportação, busca, indicadores, histórico e segurança.

- [ ] Escrever testes DOM/textuais para as quatro tarefas e ausência de ações obsoletas.
- [ ] Executar testes e confirmar falhas com a interface antiga.
- [ ] Implementar a home aprovada e fluxos curtos com conferência final.
- [ ] Executar todos os testes JavaScript administrativos.

### Task 5: Remover subsistema online e resíduos

**Files:**
- Delete: `license_service/`
- Delete: `license_service_app.py`
- Delete: `tests/test_phase7_license_service.py`
- Delete: `tests/test_phase9_license_migration.py`
- Modify: `admin_licensing/security.py`
- Modify: `license_security_app.py`
- Modify: arquivos de build, instalador e documentação que referenciem componentes removidos
- Test: `tests/test_phase7_build_cleanup.py`
- Test: `tests/test_phase10_operational_security.py`

**Interfaces:**
- Produces: chaveiro exclusivamente para chaves `license-*` e árvore sem imports/configuração de serviço ou lease.

- [ ] Escrever gate de resíduos que procure imports, símbolos, campos e executáveis removidos.
- [ ] Executar o gate e confirmar falha.
- [ ] Remover módulos, opções, chaves e documentação operacional obsoletos.
- [ ] Executar gate de resíduos e testes de segurança do chaveiro.

### Task 6: Verificação integrada

**Files:**
- Modify: testes afetados restantes para refletir exclusivamente o contrato aprovado, sem enfraquecer cobertura não relacionada.

**Interfaces:**
- Consumes: todas as tarefas anteriores.
- Produces: evidência local de consistência; nenhum artefato publicável.

- [ ] Executar `uv run --frozen --group dev ruff check .`.
- [ ] Executar `uv run --frozen --group dev python -m unittest discover -s tests -p "test_*.py"`.
- [ ] Executar os testes JavaScript declarados em `package.json`.
- [ ] Executar `git diff --check` e revisar o diff somente desta frente, preservando alterações preexistentes.
