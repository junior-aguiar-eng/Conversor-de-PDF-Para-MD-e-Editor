from __future__ import annotations

import re
import shutil
import unittest
from pathlib import Path

from markdown_utils import (
    asset_directory_name,
    available_output_path,
    finalize_markdown,
    normalize_course_heading_levels,
    normalize_heading_levels,
    split_markdown_by_headings,
)

TEST_TMP_ROOT = Path(__file__).resolve().parent / "_sandbox"


class MarkdownUtilsTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(TEST_TMP_ROOT, ignore_errors=True)

    def test_split_markdown_by_headings_returns_empty_when_text_is_short(self) -> None:
        markdown = "# Titulo\n\nTexto curto."

        self.assertEqual(split_markdown_by_headings(markdown, max_characters=200), [])

    def test_split_markdown_by_headings_preserves_preamble_and_sections(self) -> None:
        markdown = "Introducao\n\n" + "# Primeira\n\n" + "A" * 30 + "\n\n## Segunda\n\n" + "B" * 30

        chunks = split_markdown_by_headings(markdown, max_characters=60)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].startswith("Introducao\n\n# Primeira"))
        self.assertTrue(chunks[1].startswith("## Segunda"))

    def test_available_output_path_avoids_overwriting_existing_files(self) -> None:
        output_dir = TEST_TMP_ROOT / "collision"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "arquivo.md").write_text("existente", encoding="utf-8")

        next_path = available_output_path(output_dir, "arquivo")

        self.assertEqual(next_path, output_dir / "arquivo (2).md")

    def test_asset_directory_name_is_short_and_portable(self) -> None:
        name = asset_directory_name("DIREITO CONSTITUCIONAL - E-BOOK 2026 [versão final]")

        self.assertLessEqual(len(name), 49)
        self.assertRegex(name, r"^[A-Za-z0-9_-]+$")

    def test_finalize_markdown_creates_chunk_files_with_relative_image_paths(self) -> None:
        markdown = (
            "# Parte 1\n\n"
            f"![img](images/{asset_directory_name('documento')}/pagina-1.png)\n\n" + "A" * 40 + "\n\n## Parte 2\n\n" + "B" * 40
        )

        output_dir = TEST_TMP_ROOT / "chunks"
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = output_dir / "documento.md"

        result = finalize_markdown(
            source=Path("documento.pdf"),
            markdown_path=markdown_path,
            markdown=markdown,
            asset_count=1,
            split_output=True,
            max_chunk_characters=70,
        )

        self.assertEqual(result.chunk_count, 3)
        self.assertTrue(markdown_path.exists())

        chunk_files = sorted((output_dir / "documento_partes").glob("parte_*.md"))
        self.assertIn(
            f"../images/{asset_directory_name('documento')}/",
            "\n".join(path.read_text(encoding="utf-8") for path in chunk_files),
        )

    def test_normalize_heading_levels_reclassifies_comentario_label_regardless_of_origin_level(
        self,
    ) -> None:
        self.assertEqual(normalize_heading_levels("# **COMENTÁRIO:**"), "## **COMENTÁRIO:**")
        self.assertEqual(normalize_heading_levels("## **COMENTÁRIO:**"), "## **COMENTÁRIO:**")

    def test_normalize_heading_levels_reclassifies_direito_branch_to_level_one(self) -> None:
        self.assertEqual(normalize_heading_levels("## DIREITO CIVIL"), "# DIREITO CIVIL")
        self.assertEqual(normalize_heading_levels("# DIREITO CIVIL"), "# DIREITO CIVIL")

    def test_normalize_heading_levels_matches_branch_name_despite_missing_accent(self) -> None:
        # Alguns PDFs perdem acentuação na extração; a comparação deve
        # ignorar isso e reconhecer o ramo do direito do mesmo jeito.
        self.assertEqual(
            normalize_heading_levels("## DIREITO PREVIDENCIARIO"),
            "# DIREITO PREVIDENCIARIO",
        )

    def test_normalize_heading_levels_reclassifies_legal_instrument_to_level_three(self) -> None:
        heading = "## Lei n. 7.210/1984 (Lei de Execução Penal) (redação dada pela Lei n. 13.964/2019)"
        expected = "### Lei n. 7.210/1984 (Lei de Execução Penal) (redação dada pela Lei n. 13.964/2019)"

        self.assertEqual(normalize_heading_levels(heading), expected)

    def test_normalize_heading_levels_falls_back_to_level_two_for_unrecognized_titles(self) -> None:
        heading = "# Responsabilidade civil do Estado por omissão em fiscalização"
        expected = "## Responsabilidade civil do Estado por omissão em fiscalização"

        self.assertEqual(normalize_heading_levels(heading), expected)

    def test_normalize_heading_levels_is_idempotent_on_already_normalized_markdown(self) -> None:
        markdown = (
            "# DIREITO CIVIL\n\n"
            "Texto introdutório.\n\n"
            "## Responsabilidade civil do Estado por omissão\n\n"
            "Texto do julgado.\n\n"
            "## COMENTÁRIO\n\n"
            "Texto do comentário.\n\n"
            "### Lei n. 8.112/1990 (redação vigente)\n\n"
            "Texto do dispositivo citado."
        )

        self.assertEqual(normalize_heading_levels(markdown), markdown)

    def test_normalize_course_heading_levels_uses_numeric_depth_plus_one(self) -> None:
        # Profundidade 4 ("1.3.1.2" = 4 grupos) -> nível 5.
        heading = "## **1.3.1.2. ASPECTOS CONTEXTUAIS**"
        expected = "##### **1.3.1.2. ASPECTOS CONTEXTUAIS**"

        self.assertEqual(normalize_course_heading_levels(heading), expected)

    def test_normalize_course_heading_levels_tolerates_missing_final_dot(self) -> None:
        with_dot = normalize_course_heading_levels("## **1.3.1.**")
        without_dot = normalize_course_heading_levels("## **1.3.1**")

        self.assertTrue(with_dot.startswith("#### "))
        self.assertTrue(without_dot.startswith("#### "))
        self.assertEqual(without_dot, "#### **1.3.1**")

    def test_normalize_course_heading_levels_nests_uppercase_letter_under_last_numeric(
        self,
    ) -> None:
        markdown = "## 1.3. Seção\n\nTexto.\n\n## A. Item\n\nTexto do item."

        result = normalize_course_heading_levels(markdown)

        self.assertIn("### 1.3. Seção", result)
        self.assertIn("#### A. Item", result)

    def test_normalize_course_heading_levels_nests_lowercase_and_roman_under_scope(self) -> None:
        markdown = "## 1. Seção\n\n## A. Item\n\n## a) Subitem\n\n## ii) Detalhe\n\n"

        result = normalize_course_heading_levels(markdown)

        self.assertIn("## 1. Seção", result)
        self.assertIn("### A. Item", result)
        self.assertIn("#### a) Subitem", result)
        self.assertIn("##### ii) Detalhe", result)

    def test_normalize_course_heading_levels_treats_consecutive_roman_siblings_as_same_level(
        self,
    ) -> None:
        # Regressão real (Ponto 1 CONSTITUCIONAL 2026.2.md, linhas ~683-745):
        # "I."/"II."/"III." consecutivos são irmãos da mesma lista, não uma
        # cadeia pai-filho de profundidade crescente. "I." em particular é
        # ambíguo com letra maiúscula isolada (ver
        # _resolve_letter_or_roman_type) -- sem a resolução por contexto,
        # "I." caía num tipo diferente de "II."/"III." e virava pai deles.
        markdown = (
            "## **1.3.1 NEOCONSTITUCIONALISMO**\n\n"
            "## **I. Marco Histórico**\n\n"
            "Texto do marco histórico.\n\n"
            "## **II. Marco Filosófico**\n\n"
            "Texto do marco filosófico.\n\n"
            "## **III. Marco Teórico**\n\n"
            "Texto do marco teórico.\n\n"
        )

        result = normalize_course_heading_levels(markdown)

        levels = re.findall(r"^(#+) \*\*(I{1,3})\. Marco", result, flags=re.MULTILINE)
        self.assertEqual([numeral for _, numeral in levels], ["I", "II", "III"])
        self.assertEqual(len({level for level, _ in levels}), 1, f"níveis divergentes: {levels}")

    def test_normalize_course_heading_levels_treats_consecutive_lowercase_siblings_as_same_level(
        self,
    ) -> None:
        markdown = "## 1. Transformações\n\n## a) primeira mudança\n\n## b) segunda mudança\n\n## c) terceira mudança\n\n"

        result = normalize_course_heading_levels(markdown)

        levels = {len(line) - len(line.lstrip("#")) for line in result.splitlines() if re.match(r"^#+ [abc]\) ", line)}
        self.assertEqual(len(levels), 1, f"esperado nível único (irmãos), obtido níveis divergentes: {result}")

    def test_normalize_course_heading_levels_letter_after_roman_nests_under_the_roman_item(
        self,
    ) -> None:
        # Réplica do padrão real: uma letra minúscula depois de um item
        # romano deve ser filha DESSE item romano (o heading estrutural
        # mais recente), não herdar de um rastreador de "última maiúscula"
        # desatualizado de um heading anterior sem relação.
        markdown = "## **III. Marco Teórico**\n\n## b) a ampliação da jurisdição constitucional\n\n"

        result = normalize_course_heading_levels(markdown)

        roman_level = next(len(line) - len(line.lstrip("#")) for line in result.splitlines() if "Marco Teórico" in line)
        letter_level = next(
            len(line) - len(line.lstrip("#")) for line in result.splitlines() if line.strip().startswith("#") and "b)" in line
        )
        self.assertEqual(letter_level, roman_level + 1)

    def test_normalize_course_heading_levels_ambiguous_letter_does_not_falsely_continue_isolated_roman(
        self,
    ) -> None:
        # Regressão real (Ponto 1 CONSTITUCIONAL 2026.2.md, seção "1.5.
        # EVOLUÇÃO CONSTITUCIONAL DO BRASIL"): "A." / "B." abrem uma lista
        # de letras; "i." isolado (não uma lista romana de verdade, só um
        # item solto logo após B, sem relação com uma enumeração romana em
        # curso) empurra "romano" para o topo da pilha. "C." e "D." (também
        # numerais romanos válidos: C=100, D=500) então precisam continuar
        # como letra (irmãos de A/B), não "continuar" a sequência romana só
        # porque o tipo anterior era romano -- checar SÓ o tipo, sem o
        # valor numérico, tratava "C." como se fosse o próximo item depois
        # de "i." (valor 1), quebrando C/D para um nível mais profundo que
        # A/B/E/F da mesma lista.
        markdown = (
            "## 1.5. Seção\n\n"
            "## A. Constituição de 1824\n\n"
            "## B. Constituição de 1891\n\n"
            "## i. Primeira Constituição da República\n\n"
            "## C. Constituição de 1934\n\n"
            "## D. Constituição de 1937\n\n"
            "## E. Constituição de 1946\n\n"
        )

        result = normalize_course_heading_levels(markdown)

        letter_levels = {
            len(line) - len(line.lstrip("#")) for line in result.splitlines() if re.match(r"^#+ [A-E]\. Constitui", line)
        }
        self.assertEqual(len(letter_levels), 1, f"A-E deveriam ficar todos no mesmo nível (irmãos): {result}")

    def test_normalize_course_heading_levels_resumes_sibling_level_after_nested_digression(
        self,
    ) -> None:
        # Regressão descoberta ao reconverter o documento real: uma lista
        # de letras maiúsculas A..D com uma digressão em romano i)/ii)/iii)
        # aninhada DENTRO do item D -- o item seguinte "E." precisa retomar
        # o nível de A-D (irmão), não virar filho da digressão em romano.
        # Um modelo que só olha "o tipo do heading imediatamente anterior"
        # erra esse caso; requer voltar à ramificação correta na pilha.
        markdown = (
            "## 1.2.1. Marcos\n\n"
            "## A. Constitucionalismo primitivo\n\n"
            "## B. Constitucionalismo antigo\n\n"
            "## C. Constitucionalismo medieval\n\n"
            "## D. Constitucionalismo moderno\n\n"
            "## i) separação dos poderes\n\n"
            "## ii) poder constituinte\n\n"
            "## iii) supremacia do parlamento\n\n"
            "## E. Constitucionalismo contemporâneo\n\n"
        )

        result = normalize_course_heading_levels(markdown)

        letter_levels = {len(line) - len(line.lstrip("#")) for line in result.splitlines() if re.match(r"^#+ [A-E]\. ", line)}
        self.assertEqual(len(letter_levels), 1, f"A-E deveriam ficar todos no mesmo nível (irmãos): {result}")
        roman_levels = {len(line) - len(line.lstrip("#")) for line in result.splitlines() if re.match(r"^#+ i{1,3}\) ", line)}
        self.assertEqual(len(roman_levels), 1, f"i/ii/iii deveriam ficar no mesmo nível: {result}")
        self.assertEqual(
            next(iter(roman_levels)),
            next(iter(letter_levels)) + 1,
            "a digressão romana deve ser filha de D, um nível abaixo de A-E",
        )

    def test_normalize_course_heading_levels_demotes_prose_list_item_between_plain_neighbors(
        self,
    ) -> None:
        # Caso real (Ponto 1 CONSTITUCIONAL 2026.2.md, item "12." entre
        # "11." e "13."): item de lista numerada em prosa capturado como
        # heading pelo pymupdf4llm, com os vizinhos imediatos da mesma
        # numeração (11 e 13) corretamente como parágrafo comum -- deve ser
        # rebaixado a parágrafo mesmo tendo forma sintática de heading
        # numérico válido.
        markdown = (
            "11. Os provedores devem manter representante no país.\n\n"
            "## **Natureza da responsabilidade**\n\n"
            "## **12. Não haverá responsabilidade objetiva na aplicação da tese.**\n\n"
            "13. Apela-se ao Congresso Nacional para legislar sobre o tema.\n\n"
        )

        result = normalize_course_heading_levels(markdown)

        self.assertNotIn("## **12.", result)
        self.assertIn("**12. Não haverá responsabilidade objetiva", result)

    def test_normalize_course_heading_levels_keeps_heading_with_unrelated_overlapping_numbering(
        self,
    ) -> None:
        # Regressão descoberta ao validar a correção acima contra o
        # documento real: uma primeira versão do fix demovia por engano
        # dezenas de headings legítimos porque o documento tem colunas de
        # numeração PARALELAS e sem relação (lista de questões 1-N e
        # gabarito comentado citando os mesmos números em prosa) -- checar
        # só "o número aparece como parágrafo comum em algum lugar do
        # documento" colide quase sempre. O heading legítimo "12. FGV/2022,
        # TJMG..." tem "11."/"13." como HEADING vizinho (mesma lista de
        # questões), não como parágrafo -- deve permanecer heading mesmo
        # que "11."/"13." também apareçam soltos em prosa em outra parte
        # do documento, sem relação nenhuma com esta lista.
        markdown = (
            "## **11. TJRO/2019 - Juiz de Direito Substituto**\n\n"
            "Texto da questão 11.\n\n"
            "## **12. FGV/2022, TJMG - Juiz de Direito Substituto**\n\n"
            "Texto da questão 12.\n\n"
            "## **13. CEBRASPE/2022, TJMA - Juiz de Direito Substituto**\n\n"
            "Texto da questão 13.\n\n"
            "## **GABARITO COMENTADO**\n\n"
            "11. Comentário sobre a questão onze, sem relação com a lista acima.\n\n"
            "13. Comentário sobre a questão treze, sem relação com a lista acima.\n\n"
        )

        result = normalize_course_heading_levels(markdown)

        self.assertIn("## **12. FGV/2022, TJMG - Juiz de Direito Substituto**", result)

    def test_normalize_course_heading_levels_recognizes_prefix_with_fragmented_bold(
        self,
    ) -> None:
        # Caso real (Ponto 1 CONSTITUCIONAL 2026.2.md, linha ~1819): o
        # pymupdf4llm às vezes separa o negrito do prefixo e do texto em
        # dois "runs" distintos, deixando o ponto solto entre eles --
        # "**ii** . **Princípio federativo**" (dois pares de ** separados),
        # em vez de "**ii. Princípio federativo**" contínuo. Sem tolerância
        # a esse espaço, o item falha os padrões de prefixo e é rebaixado
        # por engano, ficando fora da sequência i/ii/iii/iv.
        markdown = (
            "## **i. Princípio republicano**\n\n"
            "## **ii** . **Princípio federativo**\n\n"
            "## **iii. Princípio da indissolubilidade**\n\n"
        )

        result = normalize_course_heading_levels(markdown)

        levels = {len(line) - len(line.lstrip("#")) for line in result.splitlines() if line.strip().startswith("#")}
        self.assertEqual(len(levels), 1, f"i/ii/iii deveriam ficar no mesmo nível: {result}")
        self.assertIn("## **ii** . **Princípio federativo**", result)

    def test_normalize_course_heading_levels_demotes_headings_without_structural_prefix(
        self,
    ) -> None:
        heading = "## **O Neoconstitucionalismo possui como principais características:**"

        result = normalize_course_heading_levels(heading)

        self.assertNotIn("#", result)
        self.assertEqual(
            result,
            "**O Neoconstitucionalismo possui como principais características:**",
        )

    def test_normalize_course_heading_levels_keeps_atencao_label_at_level_two(self) -> None:
        heading = "## **ATENÇÃO! JUDICIALIZAÇÃO DA SAÚDE.**"

        self.assertEqual(normalize_course_heading_levels(heading), heading)

    def test_normalize_course_heading_levels_keeps_direito_branch_at_level_one(self) -> None:
        self.assertEqual(
            normalize_course_heading_levels("# DIREITO CONSTITUCIONAL"),
            "# DIREITO CONSTITUCIONAL",
        )
        self.assertEqual(
            normalize_course_heading_levels("## DIREITO CONSTITUCIONAL"),
            "# DIREITO CONSTITUCIONAL",
        )

    def test_normalize_course_heading_levels_is_idempotent(self) -> None:
        markdown = (
            "# DIREITO CONSTITUCIONAL\n\n"
            "## 1. Neoconstitucionalismo\n\n"
            "Texto introdutório não vira heading.\n\n"
            "### 1.1. Premissas\n\n"
            "#### A. Marco histórico\n\n"
            "##### a) Constituições rígidas\n\n"
            "###### i) Efeito vinculante\n\n"
            "## **ATENÇÃO! JUDICIALIZAÇÃO DA SAÚDE.**\n\n"
        )

        once = normalize_course_heading_levels(markdown)
        twice = normalize_course_heading_levels(once)

        self.assertEqual(once, twice)

    def test_finalize_markdown_applies_course_profile_when_requested(self) -> None:
        markdown = "# DIREITO CONSTITUCIONAL\n\n## **Frase de corpo capturada por engano:**\n\n## 1. Seção real\n\n"
        output_dir = TEST_TMP_ROOT / "course_profile"
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = output_dir / "documento.md"

        finalize_markdown(
            source=Path("documento.pdf"),
            markdown_path=markdown_path,
            markdown=markdown,
            asset_count=0,
            split_output=False,
            max_chunk_characters=10_000,
            heading_profile="curso",
        )

        content = markdown_path.read_text(encoding="utf-8")
        self.assertIn("**Frase de corpo capturada por engano:**\n", content)
        self.assertNotIn("## **Frase de corpo capturada por engano:**", content)
        self.assertIn("## 1. Seção real", content)

    def test_phase4_visual_names_do_not_change_profile_markdown(self) -> None:
        complex_source = "## DIREITO CIVIL\n\n# Responsabilidade do Estado\n\n## Lei n. 8.112/1990"
        simple_source = "## 1. Tema principal\n\n## 1.1. Subtema\n\nTexto."

        self.assertEqual(
            normalize_heading_levels(complex_source),
            "# DIREITO CIVIL\n\n## Responsabilidade do Estado\n\n### Lei n. 8.112/1990",
        )
        self.assertEqual(
            normalize_course_heading_levels(simple_source),
            "## 1. Tema principal\n\n### 1.1. Subtema\n\nTexto.",
        )


if __name__ == "__main__":
    unittest.main()
