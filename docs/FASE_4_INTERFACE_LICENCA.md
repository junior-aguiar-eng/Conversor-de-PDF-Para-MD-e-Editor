# Fase 4 — Interface de licença e avisos

Status: concluída em 30/08/2026, sem commit isolado.

## Entregas

- indicador persistente de estado da licença no cabeçalho;
- central de licença com expiração comercial, dias restantes, prazo offline e última validação online;
- ações para verificar a licença local, importar `.nxjlic` e copiar o código da máquina;
- importação ACT4 por diálogo nativo, com validação de extensão e limite de 64 KB, sem expor o caminho ao renderer;
- mensagens distintas para licença ausente, válida, próxima da expiração, prazo offline encerrado, expirada, revogada, suspensa, relógio alterado, máquina divergente e licença inválida;
- avisos graduados nos marcos de 30, 15, 7, 3 e 1 dia, com abertura automática a partir de 3 dias;
- preservação da ativação ACT2/ACT3 e indicação de ausência de expiração para licenças legadas.

## Limites preservados

- a verificação manual desta fase é local e informa `online_attempted: false`;
- a consulta ao serviço remoto e a renovação do prazo offline pertencem à Fase 7;
- o encerramento do prazo offline não é apresentado como expiração comercial;
- nenhuma dependência de rede foi adicionada à renderização dos estados.

## Gate de validação

- `python -m unittest discover -s tests -p 'test_*.py'`: 244 testes aprovados;
- `node --test tests\phase1_frontend.test.js tests\phase2_renderer.test.js tests\phase4_license_ui.test.js`: 6 testes aprovados;
- `ruff check .`: aprovado;
- `python -m py_compile ...`: aprovado;
- `node --check web\app.js` e `node --check web\license-ui.js`: aprovados;
- `npm run build:web`: concluído, com `web/tailwind.css` regenerado;
- `git diff --check`: aprovado.

O commit permanece adiado para o fechamento conjunto das Fases 4, 5 e 6.
