/* Leitor offline: o conteúdo extraído é dado não confiável. */
(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("document-data").textContent);
  const content = document.getElementById("content");
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
  content.innerHTML = DOMPurify.sanitize(marked.parse(data.markdown), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "form", "input", "button", "textarea", "iframe", "object", "embed", "meta", "link", "base"],
    FORBID_ATTR: ["style", "id", "name", "srcset"],
  });
  document.title = `${data.source_file} — leitura editorial`;
  document.getElementById("document-title").textContent = data.source_file;
  const coverage = data.page_coverage || [];
  const range = coverage.length ? ` · ${coverage.length} páginas processadas` : "";
  document.getElementById("document-meta").textContent = `Leitura editorial${range} · ${data.heading_profile === "curso" ? "Material de curso" : "Jurisprudência"} · Origem: ${data.markdown_file}`;
  const warnings = coverage.filter((page) => page.status === "failed" || page.warning || page.fidelity_issues?.length)
    .map((page) => `Página ${page.page_number}: ${page.warning || (page.status === "failed" ? "não recuperada" : "conferir fidelidade")}`);
  warnings.push(...(data.editorial_issues || []));
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
    const section = data.sections[index];
    heading.id = section?.id || `section-${String(index + 1).padStart(4, "0")}`;
    const anchor = document.createElement("a");
    anchor.href = `#${heading.id}`;
    anchor.textContent = heading.textContent;
    anchor.style.paddingLeft = `${(Number(heading.tagName.slice(1)) - 1) * 10}px`;
    toc.appendChild(anchor);
  });
  if (!toc.childElementCount) toc.textContent = "Este documento não possui títulos identificados.";
  document.getElementById("print").addEventListener("click", () => window.print());
})();
