"""Funções auxiliares para preparar a saída Markdown."""

from __future__ import annotations

import re
import unicodedata
from hashlib import sha1
from pathlib import Path
from typing import Literal

from models import ConversionResult

HeadingProfile = Literal["jurisprudencia", "curso"]

HEADING_PATTERN = re.compile(r"(?m)^#{1,2}\s+.+?\s*$")
# O pymupdf4llm rankeia até 6 tamanhos de fonte distintos como níveis de
# título (1 a 6), então a saída bruta pode conter headings de nível 3+ —
# confirmado empiricamente. normalize_heading_levels precisa localizar
# TODOS eles para reclassificar; HEADING_PATTERN continua limitado a {1,2}
# de propósito para split_markdown_by_headings/build_table_of_contents, que
# devem seguir ignorando níveis 3+ mesmo depois da normalização.
RAW_HEADING_PATTERN = re.compile(r"(?m)^#{1,6}\s+.+?\s*$")
ASSET_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")
SLUG_INVALID_CHARS_PATTERN = re.compile(r"[^\w\s-]")
SLUG_WHITESPACE_PATTERN = re.compile(r"\s+")

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
# "A.", "B.", "C." — uma única letra maiúscula seguida de ponto.
COURSE_UPPERCASE_LETTER_PREFIX_PATTERN = re.compile(r"^[A-Z]\.(?:\s+|$)")
# "a)", "b)", "c)" — uma única letra minúscula seguida de parêntese.
COURSE_LOWERCASE_PAREN_PREFIX_PATTERN = re.compile(r"^[a-z]\)(?:\s+|$)")
# "i)", "ii)", "iii)", "I.", "II.", "III." — numeral romano (maiúsculo ou
# minúsculo) seguido de parêntese ou ponto. O lookahead exige ao menos um
# caractere romano válido para não casar com string vazia.
# Um único caractere ambíguo com letra isolada (I,V,X,L,C,D,M — também
# válidos como letra maiúscula/minúscula sozinha) é resolvido por contexto
# em _resolve_letter_or_roman_type, não por esta constante sozinha: ver essa
# função para o critério exato (em resumo, "I"/"i" default para romano;
# os demais só viram romano se o heading anterior já era romano).
_ROMAN_NUMERAL_CORE = r"(?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
COURSE_ROMAN_PREFIX_PATTERN = re.compile(
    rf"^{_ROMAN_NUMERAL_CORE}[).](?:\s+|$)", re.IGNORECASE
)
_ROMAN_AMBIGUOUS_LETTERS = frozenset("IVXLCDM")
# "ATENÇÃO!" — comparado contra o texto já sem negrito e sem acentuação.
COURSE_ATTENTION_PATTERN = re.compile(r"^ATENCAO!")


def split_markdown_by_headings(markdown: str, max_characters: int) -> list[str]:
    """Divide textos longos em blocos, respeitando títulos # e ##."""
    if len(markdown) <= max_characters:
        return []

    headings = list(HEADING_PATTERN.finditer(markdown))
    if not headings:
        return []

    sections: list[str] = []
    preamble = markdown[: headings[0].start()].strip()
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        section = markdown[heading.start() : end].strip()
        if index == 0 and preamble:
            section = f"{preamble}\n\n{section}"
        sections.append(section)

    chunks: list[str] = []
    current = ""
    for section in sections:
        if current and len(current) + len(section) + 2 > max_characters:
            chunks.append(current.strip())
            current = section
        else:
            current = f"{current}\n\n{section}".strip() if current else section
    if current:
        chunks.append(current.strip())
    return chunks


def _strip_accents(text: str) -> str:
    """Remove acentos para comparação tolerante a variação de acentuação
    entre PDFs de origem diferentes."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


_DIREITO_BRANCH_HEADINGS_NORMALIZED = frozenset(
    _strip_accents(name).upper() for name in DIREITO_BRANCH_HEADINGS
)
_SECTION_LABEL_HEADINGS_NORMALIZED = frozenset(
    _strip_accents(name).upper() for name in SECTION_LABEL_HEADINGS
)


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
        self._stack: list[tuple[str, int]] = []

    def register_numeric(self, level: int) -> None:
        self._stack = [("numeric", level)]

    def register_letter_or_roman(self, heading_type: str) -> int:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == heading_type:
                del self._stack[index + 1 :]
                return self._stack[index][1]
        parent_level = self._stack[-1][1] if self._stack else 1
        level = min(parent_level + 1, 6)
        self._stack.append((heading_type, level))
        return level

    @property
    def last_type(self) -> str | None:
        return self._stack[-1][0] if self._stack else None


def _resolve_letter_or_roman_type(letter: str, state: _CourseHeadingState) -> str:
    """Um único caractere de letra isolada que também é numeral romano
    válido (I,V,X,L,C,D,M) é ambíguo entre "letra maiúscula/minúscula" e
    "romano" — casa com os dois padrões ao mesmo tempo. Resolvido por
    contexto, não por prioridade fixa de regex:
    - "I"/"i" default para romano: é o primeiro item típico de uma lista
      romana, bem mais comum na prática do que uma lista de letras
      alcançar o 9º item (I é a 9ª letra) sem interrupção.
    - os demais (V,X,L,C,D,M) só viram romano se o heading estrutural mais
      recente já era romano (continuação real de sequência, ex. "IX." ->
      "X."); caso contrário, seguem como letra isolada (comportamento
      default já existente, sem essa ambiguidade eles nunca colidiriam).
    Sem essa resolução, "I." (primeiro item de uma lista romana real)
    casaria com a regra de letra maiúscula isolada antes de chegar à regra
    de romano, e ficaria num tipo diferente de "II."/"III." — a causa raiz
    do defeito de profundidade artificial crescente em listas romanas."""
    if letter.upper() not in _ROMAN_AMBIGUOUS_LETTERS:
        return "uppercase" if letter.isupper() else "lowercase"
    if letter.upper() == "I" or state.last_type == "roman":
        return "roman"
    return "uppercase" if letter.isupper() else "lowercase"


def _classify_course_heading(text: str, state: _CourseHeadingState) -> tuple[int, bool]:
    """Classifica um título do perfil de curso, atualizando o estado de
    aninhamento. Retorna (nível, mantém_como_heading) — mantém_como_heading
    é False para o rebaixamento da regra 7 (o nível retornado é ignorado
    nesse caso)."""
    canonical = _canonicalize_heading_text(text)
    normalized = _strip_accents(canonical).upper()

    if canonical.isupper() and normalized in _DIREITO_BRANCH_HEADINGS_NORMALIZED:
        return 1, True

    numeric_match = COURSE_NUMERIC_PREFIX_PATTERN.match(canonical)
    if numeric_match:
        # LIMITAÇÃO CONHECIDA (não corrigida — falta sinal estrutural
        # confiável, ver histórico de teste): esta regra não distingue
        # título de seção real de item de lista numerada em prosa (ex. "12.
        # Não haverá responsabilidade objetiva...") quando o pymupdf4llm já
        # marca esse item como heading de forma inconsistente com os
        # vizinhos da mesma lista (ex. "13." ao lado, mesma lista, não
        # marcado) — nesse caso o falso positivo é indistinguível por
        # regex de prefixo do padrão legítimo "12. FGV/2022, TJMG - Juiz
        # de Direito Substituto" de uma lista de questões. Possível pista
        # futura, NÃO implementada (precisa de mais amostras para validar):
        # heading numérico cujo texto termina em ";" ou tem muitas palavras
        # é mais provável ser item de lista em prosa do que título de
        # seção — mas arrisca falso negativo em títulos legítimos longos.
        level = _course_heading_level_from_numeric_depth(numeric_match.group(1))
        state.register_numeric(level)
        return level, True

    upper_match = COURSE_UPPERCASE_LETTER_PREFIX_PATTERN.match(canonical)
    if upper_match:
        heading_type = _resolve_letter_or_roman_type(canonical[0], state)
        return state.register_letter_or_roman(heading_type), True

    lower_match = COURSE_LOWERCASE_PAREN_PREFIX_PATTERN.match(canonical)
    if lower_match:
        heading_type = _resolve_letter_or_roman_type(canonical[0], state)
        return state.register_letter_or_roman(heading_type), True

    if COURSE_ROMAN_PREFIX_PATTERN.match(canonical):
        return state.register_letter_or_roman("roman"), True

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

    def replace(match: re.Match[str]) -> str:
        raw = match.group()
        heading_line = raw.rstrip()
        trailing_whitespace = raw[len(heading_line) :]
        level = len(heading_line) - len(heading_line.lstrip("#"))
        text = heading_line[level:].strip()
        new_level, keep_as_heading = _classify_course_heading(text, state)
        if not keep_as_heading:
            return f"{text}{trailing_whitespace}"
        return f"{'#' * new_level} {text}{trailing_whitespace}"

    return RAW_HEADING_PATTERN.sub(replace, markdown)


def _slugify_heading(text: str) -> str:
    """Gera um id de âncora no estilo GitHub a partir do texto de um título."""
    slug = SLUG_INVALID_CHARS_PATTERN.sub("", text.strip().lower())
    slug = SLUG_WHITESPACE_PATTERN.sub("-", slug)
    return slug or "secao"


def build_table_of_contents(markdown: str) -> str:
    """Gera um sumário a partir dos títulos # e ## do texto.

    Os ids de âncora seguem a convenção do GitHub, por melhor esforço: os
    links funcionam nos leitores mais comuns (GitHub, VS Code, Obsidian),
    mas nem todo visualizador de Markdown gera o mesmo id de âncora.
    """
    headings = list(HEADING_PATTERN.finditer(markdown))
    if not headings:
        return ""

    seen_slugs: dict[str, int] = {}
    lines = ["## Sumário", ""]
    for match in headings:
        heading_line = match.group().strip()
        level = len(heading_line) - len(heading_line.lstrip("#"))
        text = heading_line[level:].strip()
        slug = _slugify_heading(text)
        occurrence = seen_slugs.get(slug, 0)
        seen_slugs[slug] = occurrence + 1
        if occurrence:
            slug = f"{slug}-{occurrence}"
        indent = "  " if level == 2 else ""
        lines.append(f"{indent}- [{text}](#{slug})")
    return "\n".join(lines)


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


def finalize_markdown(
    source: Path,
    markdown_path: Path,
    markdown: str,
    asset_count: int,
    split_output: bool,
    max_chunk_characters: int,
    include_toc: bool = False,
    extraction_seconds: float = 0.0,
    heading_profile: HeadingProfile = "jurisprudencia",
) -> ConversionResult:
    # Reclassifica os níveis de título por conteúdo antes de qualquer outra
    # função consumir o texto: o corte em partes e o sumário devem ver a
    # hierarquia corrigida, não a que o pymupdf4llm inferiu da fonte do PDF.
    # heading_profile escolhe a heurística de conteúdo (boletim de
    # jurisprudência vs. material de curso com numeração hierárquica) — não
    # há detecção automática por enquanto, o chamador decide.
    if heading_profile == "curso":
        markdown = normalize_course_heading_levels(markdown)
    else:
        markdown = normalize_heading_levels(markdown)
    # O sumário é calculado a partir do markdown já normalizado e só entra
    # no arquivo principal: as partes (abaixo) continuam vindo do texto sem
    # sumário, para não gerar uma parte espúria contendo só o índice.
    chunks = split_markdown_by_headings(markdown, max_chunk_characters) if split_output else []
    toc = build_table_of_contents(markdown) if include_toc else ""
    full_markdown = f"{toc}\n\n{markdown}" if toc else markdown
    markdown_path.write_text(full_markdown, encoding="utf-8")
    if chunks:
        chunks_dir = markdown_path.parent / f"{markdown_path.stem}_partes"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for index, chunk in enumerate(chunks, start=1):
            portable_chunk = chunk.replace("images/", "../images/")
            (chunks_dir / f"parte_{index:03}.md").write_text(portable_chunk, encoding="utf-8")
    return ConversionResult(source, markdown_path, asset_count, len(chunks), extraction_seconds)
