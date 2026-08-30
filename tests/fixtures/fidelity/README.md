# Corpus de fidelidade textual

Corpus pequeno e versionado para impedir que uma conversão seja considerada confiável apenas porque retornou texto não vazio.

- `stf_re_1276977_ed.pdf`: decisão pública obtida da API oficial do STF; cobre cabeçalho em campos, rodapé de autenticação, citações e caracteres jurídicos.
- `stj_resp_612539.pdf`: inteiro teor público obtido do STJ; cobre ementa, acórdão, voto, citações e rodapé repetido.
- `cnj_manual_gestao_documental_2024.pdf`: manual público do CNJ; cobre documento longo, hierarquia, listas, notas e glossário.
- `stf_re_1276977_ed_scanned_skewed.pdf`: uma página rasterizada e inclinada, derivada do primeiro documento exclusivamente para testar OCR. Pode ser reproduzida por `build_scanned_fixture.py`.

O manifesto `corpus.json` fixa origem, SHA-256, páginas representativas e âncoras revisadas visualmente na ordem esperada. O gate falha por alteração binária, omissão, inversão das âncoras, caractere de substituição, cobertura de páginas, método de extração inesperado ou score vetorial abaixo do limite.

Os documentos são mantidos somente como fixtures de controle de qualidade, sem afirmação de licença de redistribuição além do acesso público nas fontes oficiais indicadas. Antes de distribuição comercial, confirme a política documental de cada órgão.
