# Fase 6 — Limites defensivos e serviços online

## Fronteira de validação

Os limites são aplicados pela `BridgeApi`, imediatamente antes de PyMuPDF, Edge-TTS, deep-translator e criação de executores. Controles da interface não são tratados como fronteira de confiança.

| Recurso | Limite backend | Máximo normal da UI |
|---|---:|---:|
| DPI | 36–300 | 150 |
| Imagem renderizada | 25.000.000 pixels | cerca de 2,2 milhões em A4/150 DPI |
| Área de recorte | 5.000.000 pontos² | limitada ao canvas da página |
| Coordenada PDF absoluta | 100.000 pontos | limitada à página |
| Anotações por salvamento | 2.000 | sem lote automático da UI |
| Strokes por anotação | 64 | 1 por gesto |
| Pontos por stroke | 10.000 | um por evento de ponteiro |
| Pontos de stroke por operação | 100.000 | sem máximo explícito na UI |
| Espessura de stroke | 72 pt | 36 pt |
| Fonte de anotação | 72 pt | 36 pt |
| Texto por anotação | 100.000 caracteres | sem máximo explícito na UI |
| Tradução | 100.000 caracteres, blocos de 4.500 | sem máximo explícito na UI |
| TTS | 50.000 caracteres, blocos de 5.000 | sem máximo explícito na UI |
| Workers | 4 e nunca acima do total de arquivos ou CPUs | automático |

Valores inválidos, não finitos ou acima do teto retornam respostas funcionais com `ok: false` ou `started: false`. Não são truncados silenciosamente.

## Preservação funcional

- recortes com coordenadas invertidas continuam aceitos e são normalizados;
- recortes parcialmente externos são limitados à interseção real com a página;
- aliases históricos de anotações, cores e campos continuam aceitos;
- tradução longa é remontada na ordem original dos blocos;
- TTS longo retorna segmentos MP3 e o frontend os reproduz sequencialmente;
- respostas TTS curtas preservam `audio_base64` para compatibilidade;
- o `AudioContext` de feedback é singleton, retomado sob demanda e suspenso após 1,5 segundo ocioso;
- TTS permanece em seu próprio `HTMLAudioElement`, sem compartilhar o contexto dos bipes.
