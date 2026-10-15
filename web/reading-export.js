/* Leitor offline: o conteúdo extraído é dado não confiável. */
(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("document-data").textContent);
  const content = document.getElementById("content");
  const anchorIssues = [];
  const headingKeys = new Map();
  const nonce = `${Date.now()}-${Math.random()}`;
  const sectionsByLine = new Map((data.sections || []).map((section) => [section.line, section]));
  const tokens = marked.lexer(data.markdown);
  let line = 1;
  // Só tokens de heading Markdown de topo têm uma linha indexável. HTML bruto
  // e títulos dentro de citações/listas não deslocam os IDs das seções.
  for (const token of tokens) {
    if (token.type === "heading") {
      const section = sectionsByLine.get(line);
      if (section?.level === token.depth) headingKeys.set(token, section);
    }
    line += (token.raw.match(/\n/g) || []).length;
  }
  const generated = new Map();
  const renderer = new marked.Renderer();
  renderer.heading = function (token) {
    const section = headingKeys.get(token);
    const inner = this.parser.parseInline(token.tokens);
    const probe = document.createElement("div");
    probe.innerHTML = DOMPurify.sanitize(inner, { USE_PROFILES: { html: true } });
    const expected = document.createElement("div");
    expected.innerHTML = DOMPurify.sanitize(marked.parseInline(section?.title || ""), { USE_PROFILES: { html: true } });
    const normalize = (value) => value.replace(/\s+/g, " ").trim();
    let attribute = "";
    if (section && /^section-[a-f0-9]+$|^[a-z][a-z0-9-]*$/i.test(section.id)
        && normalize(probe.textContent) === normalize(expected.textContent)) {
      const key = `${nonce}-${generated.size}`;
      generated.set(key, section);
      attribute = ` data-nxj-heading="${key}"`;
    } else {
      anchorIssues.push("Título sem correspondência verificada no índice; âncora local independente.");
    }
    return `<h${token.depth}${attribute}>${inner}</h${token.depth}>\n`;
  };
  const safeImage = (value) => {
    if (/^data:image\/(?:png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(value)) return true;
    try {
      const decoded = decodeURIComponent(value).replaceAll("\\", "/");
      return decoded.startsWith("images/") && !/[\u0000-\u0020:?#]/.test(decoded)
        && decoded.split("/").every((part) => part && part !== "." && part !== "..");
    } catch { return false; }
  };
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "IMG") {
      node.removeAttribute("srcset");
      if (!safeImage(node.getAttribute("src") || "")) node.removeAttribute("src");
    }
    if (node.tagName === "A") {
      const href = node.getAttribute("href") || "";
      if (!/^(?:https?:\/\/|mailto:|#)/i.test(href)) node.removeAttribute("href");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
  content.innerHTML = DOMPurify.sanitize(marked.parser(tokens, { renderer }), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "form", "input", "button", "textarea", "iframe", "object", "embed", "meta", "link", "base"],
    FORBID_ATTR: ["style", "id", "name", "srcset"],
  });
  if (generated.size !== (data.sections || []).length) {
    anchorIssues.push("Divergência entre títulos e índice de seções; conferir navegação.");
  }
  document.title = `${data.source_file} — leitura editorial`;
  document.getElementById("document-title").textContent = data.source_file;
  const coverage = data.page_coverage || [];
  const range = coverage.length ? ` · ${coverage.length} páginas processadas` : "";
  document.getElementById("document-meta").textContent = `Leitura editorial${range} · ${data.heading_profile === "curso" ? "Material de curso" : "Jurisprudência"} · Origem: ${data.markdown_file}`;
  const warnings = coverage.filter((page) => page.status === "failed" || page.warning || page.fidelity_issues?.length)
    .map((page) => `Página ${page.page_number}: ${page.warning || (page.status === "failed" ? "não recuperada" : "conferir fidelidade")}`);
  warnings.push(...(data.editorial_issues || []), ...new Set(anchorIssues));
  if (warnings.length) {
    const warning = document.getElementById("document-warning");
    warning.hidden = false;
    warning.textContent = `Conferência necessária:\n${warnings.join("\n")}`;
  }
  content.querySelectorAll("blockquote p:first-child").forEach((paragraph) => {
    if (paragraph.firstChild?.nodeType === 3) {
      paragraph.firstChild.textContent = paragraph.firstChild.textContent.replace(/^\[!NOTE\]\s*/, "");
    }
  });
  const toc = document.getElementById("toc");
  content.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach((heading, index) => {
    const key = heading.getAttribute("data-nxj-heading");
    const section = generated.get(key);
    heading.removeAttribute("data-nxj-heading");
    heading.id = section && !document.getElementById(section.id) ? section.id : `unindexed-heading-${index + 1}`;
    const anchor = document.createElement("a");
    anchor.href = `#${heading.id}`;
    anchor.textContent = heading.textContent;
    anchor.style.paddingLeft = `${(Number(heading.tagName.slice(1)) - 1) * 10}px`;
    toc.appendChild(anchor);
  });
  if (!toc.childElementCount) toc.textContent = "Este documento não possui títulos identificados.";
  document.getElementById("print").addEventListener("click", () => window.print());
})();
