"""Funções auxiliares para preparar a saída Markdown."""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
import uuid
from hashlib import sha1
from pathlib import Path
from typing import Literal

from models import ConversionResult, OutputReservation, PageCoverage

HeadingProfile = Literal["jurisprudencia", "curso"]
SplitMode = Literal["semantic", "strict"]

HEADING_PATTERN = re.compile(r"(?m)^#{1,2}\s+.+?\s*$")
# O pymupdf4llm rankeia até 6 tamanhos de fonte distintos como níveis de
# título (1 a 6), então a saída bruta pode conter headings de nível 3+ —
# confirmado empiricamente. normalize_heading_levels precisa localizar
# TODOS eles para reclassificar; HEADING_PATTERN continua limitado a {1,2}
# de propósito para split_markdown_by_headings, que deve seguir ignorando
# níveis 3+ mesmo depois da normalização.
RAW_HEADING_PATTERN = re.compile(r"(?m)^#{1,6}\s+.+?\s*$")
ASSET_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")

# Nomes de ramo do direito reconhecidos como título de nível 1 (ver
# normalize_heading_levels). Lista extensível: adicione novos ramos aqui,
# em maiúsculas — a comparação já ignora variação de acentuação.
DIREITO_BRANCH_HEADINGS: frozenset[str] = frozenset(
    {
        "DIREITO CONSTITUCIONAL",
        "DIREITO ADMINISTRATIVO",
        "DIREITO CIVIL",
        "DIREITO PROCESSUAL CIVIL",
        "DIREITO PREVIDENCIÁRIO",
        "DIREITO DA CRIANÇA E DO ADOLESCENTE",
        "DIREITO DIGITAL",
        "DIREITO INTERNACIONAL",
        "DIREITO AMBIENTAL",
        "DIREITO EMPRESARIAL",
        "EXECUÇÃO PENAL",
        "DIREITO PENAL",
        "DIREITO PROCESSUAL PENAL",
        "DIREITO TRIBUTÁRIO",
        "DIREITO DO CONSUMIDOR",
        "DIREITO ELEITORAL",
    }
)
# Rótulos de seção fixa reconhecidos como título de nível 2 (além do
# fallback padrão — ver normalize_heading_levels).
SECTION_LABEL_HEADINGS: frozenset[str] = frozenset({"COMENTÁRIO"})
# Início reconhecido de um cabeçalho de dispositivo legal citado (Lei,
# Código ou Constituição Federal), com anotação opcional entre parênteses.
LEGAL_INSTRUMENT_PATTERN = re.compile(r"^(CONSTITUICAO FEDERAL|LEI\b|CODIGO\b)")

# --- Perfil de normalização para material de curso (numeração hierárquica) ---
# Ver normalize_course_heading_levels. Cada padrão reconhece o prefixo
# estrutural de um tipo de heading; \s+|$ ao final tolera espaço variável
# (ou nenhum texto após o prefixo) entre o prefixo e o título propriamente
# dito.
# "1.", "1.1.", "1.1.1." etc. — ponto final do último grupo é opcional
# (visto sem ponto em amostra real, ex. "1.3.1").
COURSE_NUMERIC_PREFIX_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)\.?(?:\s+|$)")
# "A.", "B.", "C." — uma única letra maiúscula seguida de ponto. \s* antes
# do ponto tolera negrito fragmentado em dois "runs" pelo pymupdf4llm (ex.
# "**A** . **texto**" -- o "." sobra fora de qualquer negrito, com espaço
# de ambos os lados, quando a fonte de origem no PDF aplicou o negrito ao
# numeral/letra e ao texto como blocos separados; confirmado em amostra
# real, ver Ponto 1 CONSTITUCIONAL 2026.2.md, "ii" -- sem essa tolerância
# o item inteiro falha os 3 padrões de prefixo e é rebaixado por engano).
COURSE_UPPERCASE_LETTER_PREFIX_PATTERN = re.compile(r"^[A-Z]\s*\.(?:\s+|$)")
# "a)", "b)", "c)" — uma única letra minúscula seguida de parêntese.
COURSE_LOWERCASE_PAREN_PREFIX_PATTERN = re.compile(r"^[a-z]\s*\)(?:\s+|$)")
# "i)", "ii)", "iii)", "I.", "II.", "III." — numeral romano (maiúsculo ou
# minúsculo) seguido de parêntese ou ponto. O lookahead exige ao menos um
# caractere romano válido para não casar com string vazia.
# Um único caractere ambíguo com letra isolada (I,V,X,L,C,D,M — também
# válidos como letra maiúscula/minúscula sozinha) é resolvido por contexto
# em _resolve_letter_or_roman_type, não por esta constante sozinha: ver essa
# função para o critério exato (em resumo, "I"/"i" default para romano;
# os demais só viram romano se o heading anterior já era romano).
_ROMAN_NUMERAL_CORE = r"(?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
# Grupo de captura em torno do núcleo: usado para extrair o numeral e
# calcular seu valor (_roman_to_int), necessário para a checagem de
# continuidade de sequência em _resolve_letter_or_roman_type.
COURSE_ROMAN_PREFIX_PATTERN = re.compile(rf"^({_ROMAN_NUMERAL_CORE})\s*[).](?:\s+|$)", re.IGNORECASE)
_ROMAN_AMBIGUOUS_LETTERS = frozenset("IVXLCDM")
# "ATENÇÃO!" — comparado contra o texto já sem negrito e sem acentuação.
COURSE_ATTENTION_PATTERN = re.compile(r"^ATENCAO!")


def _semantic_sections(markdown: str) -> list[str]:
    """Cria unidades semânticas iniciadas por headings, sem partir seu conteúdo."""
    headings = list(RAW_HEADING_PATTERN.finditer(markdown))
    if not headings:
        return [block.strip() for block in re.split(r"\n{2,}", markdown) if block.strip()]
    sections: list[str] = []
    preamble = markdown[: headings[0].start()].strip()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        section = markdown[heading.start() : end].strip()
        if index == 0 and preamble:
            section = f"{preamble}\n\n{section}"
        sections.append(section)
    return sections


_FENCE_START = re.compile(r"^\s*(`{3,}|~{3,})")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
_IMAGE_REFERENCE = re.compile(r"!\[[^\]]*]\([^\n)]+\)")


def _markdown_blocks(markdown: str) -> list[tuple[str, bool, bool]]:
    """Retorna (texto, protegido, heading), preservando código, tabela e imagem."""
    lines = markdown.splitlines()
    blocks: list[tuple[str, bool, bool]] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        line = lines[index]
        fence = _FENCE_START.match(line)
        if fence:
            marker = fence.group(1)
            collected = [line]
            index += 1
            closing = re.compile(rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$")
            while index < len(lines):
                collected.append(lines[index])
                current = lines[index]
                index += 1
                if closing.match(current):
                    break
            blocks.append(("\n".join(collected), True, False))
            continue
        if index + 1 < len(lines) and "|" in line and _TABLE_SEPARATOR.match(lines[index + 1]):
            collected = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                collected.append(lines[index])
                index += 1
            blocks.append(("\n".join(collected), True, False))
            continue
        if _IMAGE_REFERENCE.search(line):
            blocks.append((line.strip(), True, False))
            index += 1
            continue
        collected = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            if _FENCE_START.match(lines[index]):
                break
            if index + 1 < len(lines) and "|" in lines[index] and _TABLE_SEPARATOR.match(lines[index + 1]):
                break
            if _IMAGE_REFERENCE.search(lines[index]):
                break
            collected.append(lines[index])
            index += 1
        text = "\n".join(collected).strip()
        blocks.append((text, False, bool(re.match(r"^#{1,6}\s", text))))
    return blocks


def _strict_text_pieces(text: str, max_characters: int) -> list[str]:
    pieces: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_characters:
        boundary = remaining.rfind("\n", 0, max_characters + 1)
        if boundary < max_characters // 2:
            boundary = remaining.rfind(" ", 0, max_characters + 1)
        if boundary <= 0:
            boundary = max_characters
        pieces.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _pack_chunks(units: list[str], max_characters: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if current and len(candidate) > max_characters:
            chunks.append(current.strip())
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current.strip())
    return chunks


def split_markdown(markdown: str, max_characters: int, mode: SplitMode = "semantic") -> list[str]:
    """Divide sem quebrar silenciosamente tabelas, imagens ou código cercado."""
    if len(markdown) <= max_characters:
        return []
    if mode == "semantic":
        units: list[str] = []
        for section in _semantic_sections(markdown):
            if len(section) <= max_characters:
                units.append(section)
                continue
            pending_heading = ""
            for block, _protected, heading in _markdown_blocks(section):
                if heading:
                    if pending_heading:
                        units.append(pending_heading)
                    pending_heading = block
                elif pending_heading:
                    units.append(f"{pending_heading}\n\n{block}")
                    pending_heading = ""
                else:
                    units.append(block)
            if pending_heading:
                units.append(pending_heading)
        return _pack_chunks(units, max_characters)
    if mode != "strict":
        raise ValueError("Modo de divisão inválido.")
    pieces: list[str] = []
    pending_heading = ""
    for block, protected, heading in _markdown_blocks(markdown):
        if heading:
            if pending_heading:
                pieces.append(pending_heading)
            pending_heading = block
            continue
        block_pieces = [block] if protected or len(block) <= max_characters else _strict_text_pieces(block, max_characters)
        if pending_heading:
            combined = f"{pending_heading}\n\n{block_pieces[0]}"
            if len(combined) <= max_characters:
                block_pieces[0] = combined
            elif protected:
                pieces.append(pending_heading)
            else:
                pieces.extend(_strict_text_pieces(combined, max_characters)[:-1])
                block_pieces[0] = _strict_text_pieces(combined, max_characters)[-1]
            pending_heading = ""
        pieces.extend(block_pieces)
    if pending_heading:
        pieces.append(pending_heading)
    return _pack_chunks(pieces, max_characters)


def split_markdown_by_headings(markdown: str, max_characters: int) -> list[str]:
    """Compatibilidade: o comportamento histórico passa a ser semântico."""
    return split_markdown(markdown, max_characters, "semantic")


def _strip_accents(text: str) -> str:
    """Remove acentos para comparação tolerante a variação de acentuação
    entre PDFs de origem diferentes."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


_DIREITO_BRANCH_HEADINGS_NORMALIZED = frozenset(_strip_accents(name).upper() for name in DIREITO_BRANCH_HEADINGS)
_SECTION_LABEL_HEADINGS_NORMALIZED = frozenset(_strip_accents(name).upper() for name in SECTION_LABEL_HEADINGS)


def _canonicalize_heading_text(text: str) -> str:
    """Remove marcação de negrito e normaliza espaços/dois-pontos nas bordas,
    só para fins de comparação — o texto original é preservado na saída."""
    cleaned = text.replace("**", "").replace("__", "").strip()
    cleaned = cleaned.strip(":").strip()
    return re.sub(r"\s+", " ", cleaned)


def _classify_heading_level(text: str) -> int:
    """Reclassifica o nível de um título pelo padrão do conteúdo, não pelo
    nível que o pymupdf4llm atribuiu a partir do tamanho de fonte do PDF de
    origem (que varia entre páginas sem relação com a hierarquia real).

    Regras, em ordem de prioridade — a primeira que casar decide o nível:
    1. Nome de ramo do direito conhecido, inteiramente em maiúsculas -> nível 1.
    2. Rótulo de seção fixa (ex.: "COMENTÁRIO") -> nível 2.
    3. Cabeçalho de dispositivo legal citado (Lei, Código ou Constituição
       Federal, com anotação opcional entre parênteses) -> nível 3.
    4. Qualquer outro título — assumido como julgado individual, filho do
       ramo do direito mais próximo acima no documento -> nível 2 (padrão).
    """
    canonical = _canonicalize_heading_text(text)
    normalized = _strip_accents(canonical).upper()

    if canonical.isupper() and normalized in _DIREITO_BRANCH_HEADINGS_NORMALIZED:
        return 1
    if normalized in _SECTION_LABEL_HEADINGS_NORMALIZED:
        return 2
    if LEGAL_INSTRUMENT_PATTERN.match(normalized):
        return 3
    return 2


def _normalize_heading_match(match: re.Match[str]) -> str:
    # O \s*$ do HEADING_PATTERN é guloso e pode engolir parte da linha em
    # branco que separa o título do parágrafo seguinte. Preservamos esse
    # espaço à direita na substituição para não colapsar os parágrafos.
    raw = match.group()
    heading_line = raw.rstrip()
    trailing_whitespace = raw[len(heading_line) :]
    level = len(heading_line) - len(heading_line.lstrip("#"))
    text = heading_line[level:].strip()
    new_level = _classify_heading_level(text)
    return f"{'#' * new_level} {text}{trailing_whitespace}"


def normalize_heading_levels(markdown: str) -> str:
    """Reclassifica o nível de cada título do markdown bruto (saída de
    pymupdf4llm.to_markdown, que pode conter níveis 1 a 6) por padrões de
    conteúdo (ver _classify_heading_level), preservando o texto e a
    marcação de negrito originais de cada título."""
    return RAW_HEADING_PATTERN.sub(_normalize_heading_match, markdown)


def _course_heading_level_from_numeric_depth(prefix: str) -> int:
    # Nível = profundidade do prefixo + 1 (nível 1 é reservado ao ramo do
    # direito). profundidade = número de grupos numéricos = número de
    # pontos internos ("1.3.1" tem 2 pontos = 3 grupos) + 1, então
    # nível = pontos_internos + 2. Cap em 6: não existe nível 7 no Markdown.
    return min(prefix.count(".") + 2, 6)


class _CourseHeadingState:
    """Rastreia, numa passada sequencial pelos títulos do documento, uma
    PILHA de (tipo, nível) dos headings de letra/romano (regras 3-5)
    atualmente "abertos" — do mais raso (base) ao mais profundo (topo).

    A profundidade relativa entre tipos NÃO é fixa (numérico < maiúscula <
    minúscula < romano não vale sempre): depende de como cada tipo é usado
    em CADA ramificação do documento real. Ex. confirmado em
    Ponto 1 CONSTITUCIONAL 2026.2.md: romano aparece tanto raso (marcos "I./
    II./III." direto sob uma seção numérica, com letra minúscula "b)"
    aninhada como FILHA de "III.") quanto profundo (letras "A.".."H.", com
    uma digressão em romano "i)/ii)/iii)" aninhada DENTRO do item "D.",
    e a letra seguinte "E." precisa retomar o nível de A-D, não virar filha
    da digressão). Um rank fixo por tipo não satisfaz os dois padrões ao
    mesmo tempo — só decidir pela pilha real de cada ramificação resolve.

    Regra: ao ver um heading de tipo T, se T já está aberto em algum ponto
    da pilha (não só no topo), "volta" a essa ramificação — é IRMÃO do
    heading que abriu esse tipo, descartando tudo que foi empilhado depois
    dele (ex.: E volta ao nível de D, descartando a digressão em romano).
    Se T é um tipo novo nesta ramificação, é FILHO do heading mais
    profundo atualmente aberto (topo da pilha).

    Um heading numérico (regra 2) sempre reinicia a pilha do zero — nenhum
    tipo de letra/romano de uma seção numerada anterior continua aberto."""

    def __init__(self) -> None:
        self._stack: list[tuple[str, int, int | None]] = []

    def register_numeric(self, level: int) -> None:
        self._stack = [("numeric", level, None)]

    def register_letter_or_roman(self, heading_type: str, roman_value: int | None = None) -> int:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == heading_type:
                del self._stack[index + 1 :]
                self._stack[index] = (heading_type, self._stack[index][1], roman_value)
                return self._stack[index][1]
        parent_level = self._stack[-1][1] if self._stack else 1
        level = min(parent_level + 1, 6)
        self._stack.append((heading_type, level, roman_value))
        return level

    @property
    def last_type(self) -> str | None:
        return self._stack[-1][0] if self._stack else None

    @property
    def last_roman_value(self) -> int | None:
        return self._stack[-1][2] if self._stack else None


_ROMAN_NUMERAL_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(roman: str) -> int:
    total = 0
    previous_value = 0
    for char in reversed(roman.upper()):
        value = _ROMAN_NUMERAL_VALUES[char]
        if value < previous_value:
            total -= value
        else:
            total += value
            previous_value = value
    return total


def _resolve_letter_or_roman_type(letter: str, state: _CourseHeadingState) -> tuple[str, int | None]:
    """Um único caractere de letra isolada que também é numeral romano
    válido (I,V,X,L,C,D,M) é ambíguo entre "letra maiúscula/minúscula" e
    "romano" — casa com os dois padrões ao mesmo tempo. Resolvido por
    contexto, não por prioridade fixa de regex. Retorna (tipo, valor_romano
    — só quando tipo="roman", senão None):
    - "I"/"i" default para romano (valor 1): é o primeiro item típico de
      uma lista romana, bem mais comum na prática do que uma lista de
      letras alcançar o 9º item (I é a 9ª letra) sem interrupção.
    - os demais (V,X,L,C,D,M) só viram romano se o heading estrutural mais
      recente já era romano E o valor deste caractere é exatamente o
      PRÓXIMO da sequência (ex. "IX." valor 9 -> "X." valor 10 continua;
      mas um "i." isolado (valor 1) seguido de "C." (valor 100) NÃO é uma
      continuação real — "C." é o início de uma lista de letras nova,
      mesmo com o tipo anterior sendo romano por coincidência). Checar só
      "o tipo anterior era romano" (sem o valor) foi tentado e descartado:
      confirmado bug real em Ponto 1 CONSTITUCIONAL 2026.2.md — depois de
      um "i. Primeira Constituição..." isolado (não uma lista romana de
      verdade, só um item solto), "C. Constituição de 1934" era
      erroneamente tratado como romano só por coincidir tipo, quebrando o
      nível de C/D em relação a A/B/E/F da mesma lista de letras.
    Sem NENHUMA resolução por contexto, "I." (primeiro item de uma lista
    romana real) casaria com a regra de letra maiúscula isolada antes de
    chegar à regra de romano, e ficaria num tipo diferente de "II."/"III."
    — a causa raiz original do defeito de profundidade artificial crescente
    em listas romanas."""
    if letter.upper() not in _ROMAN_AMBIGUOUS_LETTERS:
        return ("uppercase" if letter.isupper() else "lowercase"), None
    if letter.upper() == "I":
        return "roman", 1
    value = _roman_to_int(letter.upper())
    if state.last_type == "roman" and state.last_roman_value == value - 1:
        return "roman", value
    return ("uppercase" if letter.isupper() else "lowercase"), None


_HEADING_LINE_START_RE = re.compile(r"^#{1,6}(\s|$)")


def _prose_list_item_positions(markdown: str) -> frozenset[int]:
    """Posições (offset de caractere no markdown original) de heading
    numérico TOP-LEVEL (ex. "12.", nunca a coluna de sub-numeração "12.3")
    cujo vizinho numérico IMEDIATO — a linha numerada mais próxima antes e
    depois dele, heading ou parágrafo comum, na mesma lista — é parágrafo
    comum com número-1/número+1. Sinal estrutural de item de lista em prosa
    capturado como heading por engano, não título de seção real (caso real:
    Ponto 1 CONSTITUCIONAL 2026.2.md, "12. Não haverá responsabilidade..."
    entre "11." e "13.", ambos parágrafo comum).

    Deliberadamente por PROXIMIDADE POSICIONAL, não "o número aparece como
    parágrafo comum em algum ponto do documento" — essa versão mais simples
    foi tentada e descartada: o mesmo documento tem colunas de numeração
    paralelas e sem relação (lista de questões 1-22, cada uma heading
    legítimo tipo "12. FGV/2022, TJMG..."; gabarito comentado citando os
    mesmos números 1-22 em prosa) que colidem em quase todo número sem o
    filtro de adjacência real, demovendo dezenas de headings legítimos."""
    entries: list[tuple[int, int, bool]] = []
    position = 0
    for line in markdown.split("\n"):
        stripped = line.strip()
        is_heading_line = bool(_HEADING_LINE_START_RE.match(stripped))
        content = stripped.lstrip("#").strip() if is_heading_line else stripped
        canonical = _canonicalize_heading_text(content)
        number_match = COURSE_NUMERIC_PREFIX_PATTERN.match(canonical)
        if number_match and "." not in number_match.group(1):
            entries.append((position, int(number_match.group(1)), is_heading_line))
        position += len(line) + 1

    prose_positions: set[int] = set()
    for index, (entry_position, number, is_heading_line) in enumerate(entries):
        if not is_heading_line:
            continue
        previous_matches = index > 0 and not entries[index - 1][2] and entries[index - 1][1] == number - 1
        next_matches = index + 1 < len(entries) and not entries[index + 1][2] and entries[index + 1][1] == number + 1
        if previous_matches and next_matches:
            prose_positions.add(entry_position)
    return frozenset(prose_positions)


def _classify_course_heading(text: str, state: _CourseHeadingState) -> tuple[int, bool]:
    """Classifica um título do perfil de curso, atualizando o estado de
    aninhamento. Retorna (nível, mantém_como_heading) — mantém_como_heading
    é False para o rebaixamento da regra 7 (o nível retornado é ignorado
    nesse caso). O rebaixamento por continuidade de sequência numérica
    (Defeito 2) acontece ANTES desta função, em normalize_course_heading_levels
    — ver _prose_list_item_positions — porque depende da posição do heading
    no documento, não só do seu próprio texto."""
    canonical = _canonicalize_heading_text(text)
    normalized = _strip_accents(canonical).upper()

    if canonical.isupper() and normalized in _DIREITO_BRANCH_HEADINGS_NORMALIZED:
        return 1, True

    numeric_match = COURSE_NUMERIC_PREFIX_PATTERN.match(canonical)
    if numeric_match:
        level = _course_heading_level_from_numeric_depth(numeric_match.group(1))
        state.register_numeric(level)
        return level, True

    upper_match = COURSE_UPPERCASE_LETTER_PREFIX_PATTERN.match(canonical)
    if upper_match:
        heading_type, roman_value = _resolve_letter_or_roman_type(canonical[0], state)
        return state.register_letter_or_roman(heading_type, roman_value), True

    lower_match = COURSE_LOWERCASE_PAREN_PREFIX_PATTERN.match(canonical)
    if lower_match:
        heading_type, roman_value = _resolve_letter_or_roman_type(canonical[0], state)
        return state.register_letter_or_roman(heading_type, roman_value), True

    roman_match = COURSE_ROMAN_PREFIX_PATTERN.match(canonical)
    if roman_match:
        roman_value = _roman_to_int(roman_match.group(1))
        return state.register_letter_or_roman("roman", roman_value), True

    if COURSE_ATTENTION_PATTERN.match(normalized):
        return 2, True

    return 0, False


def normalize_course_heading_levels(markdown: str) -> str:
    """Reclassifica o nível de cada título do markdown bruto pelo perfil de
    material de curso (numeração hierárquica "1.", "1.1.", "A.", "a)",
    "i)" etc.), como alternativa a normalize_heading_levels (perfil de
    boletim de jurisprudência).

    Diferente do outro perfil, esta função primeiro faz uma triagem: título
    sem prefixo estrutural reconhecível é rebaixado a parágrafo comum (a
    marcação de heading é removida), pois na amostra real ~30% dos títulos
    que o pymupdf4llm gera aqui são frases de corpo de texto capturadas por
    engano. Títulos legítimos são então reclassificados pela profundidade
    indicada no próprio prefixo do texto, não pelo nível herdado do
    pymupdf4llm — por isso a função percorre os títulos em ordem sequencial,
    mantendo estado de aninhamento (_CourseHeadingState), em vez de
    classificar cada um isoladamente.
    """
    state = _CourseHeadingState()
    prose_list_item_positions = _prose_list_item_positions(markdown)

    def replace(match: re.Match[str]) -> str:
        raw = match.group()
        heading_line = raw.rstrip()
        trailing_whitespace = raw[len(heading_line) :]
        level = len(heading_line) - len(heading_line.lstrip("#"))
        text = heading_line[level:].strip()
        if match.start() in prose_list_item_positions:
            return f"{text}{trailing_whitespace}"
        new_level, keep_as_heading = _classify_course_heading(text, state)
        if not keep_as_heading:
            return f"{text}{trailing_whitespace}"
        return f"{'#' * new_level} {text}{trailing_whitespace}"

    return RAW_HEADING_PATTERN.sub(replace, markdown)


def available_output_path(output_dir: Path, stem: str) -> Path:
    """Retorna um caminho livre, sem substituir uma conversão já existente."""
    candidate = output_dir / f"{stem}.md"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{stem} ({index}).md"
        index += 1
    return candidate


def asset_directory_name(stem: str) -> str:
    """Gera um nome curto e seguro para os recursos extraídos do PDF."""
    normalized = ASSET_NAME_PATTERN.sub("_", stem).strip("_-") or "documento"
    return f"{normalized[:40]}-{sha1(stem.encode('utf-8')).hexdigest()[:8]}"


def output_paths(output_dir: Path, source: Path) -> tuple[Path, Path]:
    markdown_path = available_output_path(output_dir, source.stem)
    return markdown_path, output_dir / "images" / asset_directory_name(markdown_path.stem)


def reserve_batch_output_paths(
    output_dir: Path,
    sources: list[Path],
    *,
    retry: bool = False,
) -> list[OutputReservation]:
    """Reserva nomes exclusivos para todo o lote antes de iniciar workers."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    reserved: set[str] = set()
    reservations: list[OutputReservation] = []
    suffix = " - páginas reprocessadas" if retry else ""
    for source in sources:
        stem = f"{source.stem}{suffix}"
        index = 1
        while True:
            candidate_stem = stem if index == 1 else f"{stem} ({index})"
            markdown_path = output_dir / f"{candidate_stem}.md"
            assets_dir = output_dir / "images" / asset_directory_name(candidate_stem)
            chunks_dir = output_dir / f"{candidate_stem}_partes"
            key = str(markdown_path).casefold()
            if key not in reserved and not markdown_path.exists() and not assets_dir.exists() and not chunks_dir.exists():
                reserved.add(key)
                reservations.append(OutputReservation(markdown_path, assets_dir, chunks_dir))
                break
            index += 1
    return reservations


def finalize_markdown(
    source: Path,
    markdown_path: Path,
    markdown: str,
    asset_count: int,
    split_output: bool,
    max_chunk_characters: int,
    extraction_seconds: float = 0.0,
    heading_profile: HeadingProfile = "jurisprudencia",
    split_mode: SplitMode = "semantic",
    page_coverage: tuple[PageCoverage, ...] = (),
    reservation: OutputReservation | None = None,
    temporary_assets_dir: Path | None = None,
) -> ConversionResult:
    # Reclassifica os níveis de título por conteúdo antes de qualquer outra
    # função consumir o texto: o corte em partes deve ver a hierarquia
    # corrigida, não a que o pymupdf4llm inferiu da fonte do PDF.
    # heading_profile escolhe a heurística de conteúdo (boletim de
    # jurisprudência vs. material de curso com numeração hierárquica) — não
    # há detecção automática por enquanto, o chamador decide.
    if heading_profile == "curso":
        markdown = normalize_course_heading_levels(markdown)
    else:
        markdown = normalize_heading_levels(markdown)
    chunks = split_markdown(markdown, max_chunk_characters, split_mode) if split_output else []
    target = reservation or OutputReservation(
        markdown_path,
        markdown_path.parent / "images" / asset_directory_name(markdown_path.stem),
        markdown_path.parent / f"{markdown_path.stem}_partes",
    )
    token = uuid.uuid4().hex
    markdown_temp = target.markdown_path.with_name(f".{target.markdown_path.name}.{token}.tmp")
    chunks_temp = target.chunks_dir.with_name(f".{target.chunks_dir.name}.{token}.tmp")
    promoted_assets = False
    promoted_chunks = False
    promoted_markdown = False
    try:
        if target.markdown_path.exists() or target.assets_dir.exists() or target.chunks_dir.exists():
            raise FileExistsError(f"Destino reservado deixou de estar disponível: {target.markdown_path}")
        markdown_temp.write_text(markdown, encoding="utf-8")
        if chunks:
            chunks_temp.mkdir(parents=True, exist_ok=False)
            for index, chunk in enumerate(chunks, start=1):
                portable_chunk = chunk.replace("images/", "../images/")
                (chunks_temp / f"parte_{index:03}.md").write_text(portable_chunk, encoding="utf-8")
        if asset_count and temporary_assets_dir is not None:
            target.assets_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary_assets_dir, target.assets_dir)
            promoted_assets = True
        elif temporary_assets_dir is not None:
            shutil.rmtree(temporary_assets_dir, ignore_errors=True)
        if chunks:
            os.replace(chunks_temp, target.chunks_dir)
            promoted_chunks = True
        os.replace(markdown_temp, target.markdown_path)
        promoted_markdown = True
    except Exception:
        markdown_temp.unlink(missing_ok=True)
        shutil.rmtree(chunks_temp, ignore_errors=True)
        if temporary_assets_dir is not None:
            shutil.rmtree(temporary_assets_dir, ignore_errors=True)
        if promoted_markdown:
            target.markdown_path.unlink(missing_ok=True)
        if promoted_chunks:
            shutil.rmtree(target.chunks_dir, ignore_errors=True)
        if promoted_assets:
            shutil.rmtree(target.assets_dir, ignore_errors=True)
        raise
    return ConversionResult(source, target.markdown_path, asset_count, len(chunks), extraction_seconds, page_coverage)
