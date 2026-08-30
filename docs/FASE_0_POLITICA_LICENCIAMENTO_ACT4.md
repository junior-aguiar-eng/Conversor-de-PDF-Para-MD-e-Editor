# Fase 0 — Política de licenciamento ACT4

**Status:** aprovada

**Data:** 30/08/2026

**Escopo:** novas licenças `ACT4`; compatibilidade com `ACT2` e `ACT3`

## 1. Princípios

1. A licença concede acesso por prazo determinado e por dispositivo autorizado.
2. A validação de licença não envia PDFs, Markdown, anotações, conteúdo do acervo,
   nomes de arquivos nem outros dados documentais.
3. Uma indisponibilidade temporária do serviço não bloqueia imediatamente uma
   licença regular.
4. Licenças já emitidas não recebem prazo, obrigação de conexão ou revogação por
   alteração silenciosa de software.
5. O aplicativo preserva o acesso aos dados do usuário mesmo quando as funções
   licenciadas estiverem bloqueadas.
6. Toda mudança de dispositivo, renovação, suspensão, revogação e reativação deve
   ser explícita e auditável.

## 2. Política comercial padrão

| Item | Política proposta |
|---|---|
| Prazo comercial | 3, 6 ou 12 meses, contado de `not_before` até `expires_at` |
| Dispositivos | 1 computador ativo por licença |
| Consulta online | A cada 24 horas, somente para licenças emitidas em modo híbrido |
| Tolerância offline | 7 dias após a última validação online confiável |
| Avisos de expiração | 30, 15, 7, 3 e 1 dia antes de `expires_at` |
| Renovação | Novo vencimento definido pelo administrador, sem alteração retroativa |
| Transferência | Desvinculação administrativa do dispositivo anterior e emissão para o novo |

O prazo deve ser escolhido entre 3, 6 ou 12 meses. A quantidade de dispositivos
pode variar por contrato. Ambos devem constar expressamente na licença emitida;
a ausência de um valor obrigatório torna o payload inválido, sem defaults ocultos
no Conversor.

## 3. Modos de validação e implantação gradual

Há dois modos explícitos para novas licenças `ACT4`:

- `offline`: o arquivo `.nxjlic` autoriza o uso até `expires_at`, sem consulta
  periódica. Suspensão e revogação dependem da importação de nova licença ou do
  encerramento do prazo comercial.
- `hybrid`: o arquivo `.nxjlic` define o direito comercial, e um lease assinado
  comprova o estado online por até `max_offline_days`. A licença continua
  utilizável durante o lease vigente quando o serviço estiver indisponível.

O primeiro incremento utilizável será emitido em modo `offline`. A futura entrada
do serviço não converterá licenças offline existentes para o modo híbrido. Essa
migração exigirá ato administrativo explícito e novo arquivo de licença.

O protocolo `ACT4` deve, portanto, representar o modo de validação no payload.
Sem esse campo, a regra de sete dias entraria em conflito com a implantação local
anterior ao serviço.

## 4. Indisponibilidade do serviço

Para licença `hybrid`:

1. se a consulta falhar e o lease ainda estiver vigente, as funções autorizadas
   continuam disponíveis;
2. o aplicativo informa a falha e o tempo offline restante, sem confundi-lo com
   o vencimento comercial;
3. esgotado o lease, o estado passa a `online_check_required` e as funções
   licenciadas ficam bloqueadas até uma validação online válida;
4. a indisponibilidade do serviço nunca amplia `expires_at`;
5. uma resposta online inválida, não assinada ou incompatível não substitui o
   último estado confiável.

Para licença `offline`, a indisponibilidade do serviço não altera o direito de uso.

## 5. Computadores e transferência

- A licença padrão autoriza um `machine_id` por vez.
- Não haverá transferência automática no Conversor.
- O administrador registra o motivo, encerra o vínculo anterior e emite uma nova
  licença para o novo `machine_id`.
- Não se fixa limite numérico automático de transferências nesta primeira versão;
  casos repetidos ficam sujeitos a análise administrativa e permanecem auditados.
- Em modo híbrido, o dispositivo anterior pode permanecer funcional somente até
  o fim do lease já emitido. Em modo offline, não há revogação remota imediata;
  essa limitação deve ser conhecida antes da transferência.
- Reinstalação na mesma máquina não consome transferência quando o `machine_id`
  permanecer igual.

## 6. Compatibilidade com ACT2 e ACT3

- `ACT2` e `ACT3` válidas continuam aceitas pelo caminho legado atual.
- Não recebem `expires_at`, lease, suspensão ou revogação retroativamente.
- Não são convertidas silenciosamente em `ACT4`.
- A migração ocorre somente por operação administrativa explícita, com ciência do
  novo prazo e do modo `offline` ou `hybrid`.
- A primeira versão compatível com `ACT4` não exige o serviço online para uma
  instalação legitimamente ativada com `ACT2` ou `ACT3`.
- A remoção futura da compatibilidade legada depende de decisão comercial e
  contratual própria, fora desta frente técnica.

## 7. Funções licenciadas e comportamento após bloqueio

As features iniciais de `ACT4` são `converter`, `ocr` e `reader`. A autorização é
avaliada por feature; não se presume acesso a uma função ausente do payload.

Quando a licença estiver expirada, suspensa, revogada, vinculada a outra máquina,
com lease esgotado ou for inválida:

- iniciar ou retomar conversão e OCR fica bloqueado antes de criar saídas ou
  disparar workers;
- recursos do leitor marcados como `reader` ficam bloqueados para novos trabalhos;
- arquivos do usuário, Markdown já convertido, PDFs originais, banco do acervo,
  backups e configurações não são apagados, criptografados nem tornados
  irrecuperáveis;
- permanecem disponíveis a consulta do status, a cópia do código da máquina e a
  importação de licença;
- conversões já concluídas continuam sendo arquivos comuns do usuário.

Uma operação protegida em andamento ao alcançar `expires_at` ou o fim do lease
deve encerrar no próximo limite seguro e persistir estado recuperável. Ela não
inicia nova página ou novo documento depois do bloqueio. O comportamento técnico
detalhado será fechado na máquina de estados da Fase 2.

## 8. Dados mínimos enviados ao serviço

O cliente envia somente:

- versão do protocolo;
- `license_id`;
- `machine_id` ou identificador de dispositivo derivado e estável;
- `key_id` da licença;
- versão do aplicativo;
- nonce aleatório por requisição;
- horário local declarado, usado apenas como sinal auxiliar;
- identificador do lease anterior, quando houver.

O serviço pode registrar o horário do servidor, resultado, endereço IP para
segurança operacional, identificadores acima e eventos administrativos. Não deve
receber nome do cliente, e-mail, CPF/CNPJ ou referência comercial na chamada do
Conversor quando `license_id` for suficiente para a resolução interna.

São expressamente proibidos na API de licenciamento:

- conteúdo ou hash de PDFs, Markdown e anotações;
- nomes, caminhos, quantidade ou metadados dos documentos;
- conteúdo do acervo e termos pesquisados;
- senhas, chave privada, chave de licença completa ou diagnósticos gerais da
  máquina sem finalidade estrita de licenciamento.

Logs técnicos devem aplicar retenção definida, controle de acesso e minimização.
Uma política operacional de retenção será aprovada antes da implantação do
serviço.

## 9. Datas, fuso e avisos

- Todos os instantes do protocolo usam UTC no formato RFC 3339 com sufixo `Z`.
- `not_before` é inclusivo e `expires_at` é exclusivo.
- O prazo termina no instante exato de `expires_at`, não no fim do dia local.
- A interface converte datas para o fuso do usuário apenas para exibição.
- Avisos são calculados pelo tempo restante real; cada marco é exibido uma vez por
  abertura conforme a severidade definida na Fase 4.
- Retrocesso relevante do relógio não antecipa nem prorroga direitos e será tratado
  na Fase 3.

## 10. Chaves e separação de responsabilidades

- A chave principal assina arquivos `.nxjlic` completos.
- Uma chave online distinta assina leases temporários.
- Cada chave é identificada por `key_id`; o Conversor contém apenas chaves
  públicas confiáveis.
- Chaves privadas não entram no Conversor, instalador ou repositório distribuído.
- Rotação adiciona a nova chave pública antes de iniciar emissões com ela.
- A retirada de chave pública antiga somente ocorre quando nenhuma licença ou
  lease ainda válido depender dela, salvo resposta específica a comprometimento.

## 11. Decisões que este documento fixa para as fases seguintes

1. `ACT4` deve conter um campo obrigatório de modo de validação.
2. O período offline de sete dias aplica-se apenas ao modo `hybrid`.
3. A primeira entrega local usa `offline` e não depende do serviço.
4. `ACT2` e `ACT3` permanecem vitalícias nos termos técnicos atuais até migração
   explícita.
5. Bloqueio de licença não implica perda dos dados do usuário.
6. O Conversor não envia dados documentais ao serviço de licenças.

## 12. Gate da Fase 0

A política foi aprovada pelo titular do produto em 30/08/2026. Alterações futuras
nas regras comerciais exigem revisão explícita deste documento antes de serem
incorporadas ao protocolo ou ao aplicativo.

Não houve alteração de código nesta fase.
