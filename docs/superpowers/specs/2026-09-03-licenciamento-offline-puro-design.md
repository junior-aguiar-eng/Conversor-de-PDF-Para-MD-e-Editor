# Licenciamento offline puro — desenho

## Objetivo

Substituir o licenciamento híbrido por um sistema exclusivamente offline, mantendo licenças temporárias de 3, 6 ou 12 meses, vínculo por computador, assinatura Ed25519, renovação e auditoria. Não existem licenças reais emitidas; compatibilidade com documentos de teste, ACT2, ACT3, leases ou bancos administrativos anteriores não é requisito.

## Modelo de confiança

O Admin conserva a chave privada criptografada e emite documentos `.nxjlic`. O Conversor distribui somente chaves públicas e valida localmente assinatura, estrutura, revisão, máquina, recursos e prazo. Nenhum fluxo de ativação depende de rede.

Revogação e suspensão de licenças não existem. Um documento já emitido permanece válido até `expires_at`. A troca de computador emite uma nova revisão para o novo `machine_id`, preservando `license_id`, recursos e vencimento; o documento anterior pode continuar válido na máquina antiga até o mesmo vencimento.

## Protocolo ACT4

O payload assinado contém exatamente `schema`, `key_id`, `license_id`, `revision`, `machine_id`, `issued_at`, `not_before`, `expires_at`, `features` e `customer_reference`. O envelope contém formato, payload e assinatura.

`revision` é um inteiro positivo e crescente por licença. O cliente rejeita a importação de revisão inferior à já armazenada para o mesmo `license_id`. `not_before` é inclusivo e `expires_at` exclusivo. Os recursos válidos permanecem `converter`, `ocr` e `reader`.

Saem do protocolo `validation_mode` e `max_offline_days`. Saem do runtime leases, consulta periódica, estados `online_check_required`, `suspended` e `revoked`, tempo confiável do servidor e compatibilidade ACT2/ACT3. Permanece a proteção temporal local contra retrocesso em relação ao último instante observado; ela não promete resistir a um administrador da máquina que apague coordenadamente todo o estado local.

## Ciclo de vida

- Emissão: cria a licença e a revisão 1, vinculada ao computador informado.
- Renovação: mantém licença e computador, cria a próxima revisão e soma 3, 6 ou 12 meses ao vencimento atual quando ainda válido; quando expirado, conta do instante da renovação.
- Troca de computador: mantém licença, recursos e vencimento, cria a próxima revisão para o novo computador e registra máquina anterior e nova.
- Reexportação: grava novamente os bytes da revisão vigente, sem criar revisão ou alterar direitos.
- Estado: `valid`, `expiring` e `expired` são derivados do prazo; `unlicensed`, `invalid`, `clock_tampered` e `machine_mismatch` descrevem falhas locais.

## Persistência administrativa

O banco novo contém clientes, licenças, recursos da licença, revisões imutáveis, exportações, usuários administrativos e eventos de auditoria. A licença guarda a revisão vigente; cada revisão guarda o documento assinado e os valores históricos de máquina e prazo. Exportações registram hash e momento, sem caminho do arquivo.

São removidas as estruturas de leases, mudanças de status, migração legada e dispositivo ativo separado. Bancos do modelo anterior são incompatíveis: a operação de atualização deve fazer backup explícito antes de inicializar o novo banco, sem conversão silenciosa.

## Admin visual

A página inicial é orientada a tarefas, com quatro ações principais: emitir, renovar, trocar computador e reexportar. Busca por cliente, licença ou máquina e indicadores de válidas, próximas do vencimento e expiradas ficam na mesma tela. Clientes, histórico e segurança/backup permanecem na navegação secundária.

Emissão usa três etapas: cliente e máquina; condições; conferência. As outras tarefas começam pela busca da licença e exibem comparação entre estado atual e resultado. Campos alheios à operação não aparecem. Emissão/revisão e exportação são eventos separados: falha ao escolher ou gravar o destino não desfaz a revisão, que pode ser reexportada.

## Remoções

Remover o serviço HTTPS de licenças, cliente/configuração online, protocolo de lease, chave de lease, ferramentas de suspensão/revogação/reativação, migração ACT2/ACT3 e documentação/testes exclusivos desses fluxos. A rotação e a marcação de comprometimento da chave principal permanecem como segurança da assinatura, sem efeito retroativo sobre licenças já emitidas.

## Validação

Os testes devem cobrir parsing estrito e assinatura; emissão, renovação, troca e reexportação; revisão crescente e anti-rollback local; expiração e retrocesso do relógio; interface orientada a tarefas; ausência de referências online no build; backup/restauração do banco e do chaveiro; suíte Python, testes JavaScript, Ruff e gates de resíduos.

## Limites desta execução

Implementação local somente. Commit, build, instalação, publicação e operação com chaves ou bancos reais são gates separados.
