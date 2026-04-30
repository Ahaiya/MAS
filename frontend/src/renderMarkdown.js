/**
 * Minimal Markdown → HTML renderer (no dependencies).
 * Handles: headings, fenced code blocks, blockquotes,
 *          unordered/ordered lists, bold, italic, inline code, links, hr.
 */

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderInline(text) {
  // Escape first, then apply inline patterns (which insert safe HTML tags).
  return escapeHtml(text)
    .replace(/\*\*\*(.+?)\*\*\*/gs, "<strong><em>$1</em></strong>")
    .replace(/\*\*(.+?)\*\*/gs, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/gs, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}

export function renderMarkdown(input) {
  const lines = String(input ?? "").split("\n");
  const result = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    // Fenced code block
    const fenceMatch = line.match(/^(`{3,}|~{3,})(\w*)/);
    if (fenceMatch) {
      const fence = fenceMatch[1];
      const lang = fenceMatch[2] || "";
      const codeLines = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith(fence)) {
        codeLines.push(lines[index]);
        index += 1;
      }
      index += 1; // skip closing fence
      const langAttr = lang ? ` class="language-${escapeHtml(lang)}"` : "";
      result.push(`<pre><code${langAttr}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    // ATX heading
    const headingMatch = line.match(/^(#{1,6})\s+(.*)/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      result.push(`<h${level}>${renderInline(headingMatch[2])}</h${level}>`);
      index += 1;
      continue;
    }

    // Horizontal rule
    if (/^[-*_]{3,}\s*$/.test(line)) {
      result.push("<hr>");
      index += 1;
      continue;
    }

    // Blank line — skip (paragraph separation handled by block boundaries)
    if (!line.trim()) {
      index += 1;
      continue;
    }

    // Blockquote
    if (line.startsWith("> ") || line === ">") {
      const quoteLines = [];
      while (index < lines.length && (lines[index].startsWith("> ") || lines[index] === ">")) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      result.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }

    // Unordered list
    if (/^[-*+]\s/.test(line)) {
      const items = [];
      while (index < lines.length && /^[-*+]\s/.test(lines[index])) {
        items.push(`<li>${renderInline(lines[index].replace(/^[-*+]\s+/, ""))}</li>`);
        index += 1;
      }
      result.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    // Ordered list
    if (/^\d+\.\s/.test(line)) {
      const items = [];
      while (index < lines.length && /^\d+\.\s/.test(lines[index])) {
        items.push(`<li>${renderInline(lines[index].replace(/^\d+\.\s+/, ""))}</li>`);
        index += 1;
      }
      result.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    // Paragraph: collect consecutive non-blank, non-structural lines
    const paraLines = [];
    while (
      index < lines.length
      && lines[index].trim()
      && !lines[index].match(/^#{1,6}\s/)
      && !lines[index].match(/^[-*_]{3,}\s*$/)
      && !lines[index].match(/^(`{3,}|~{3,})/)
      && !lines[index].startsWith("> ")
      && !lines[index].match(/^[-*+]\s/)
      && !lines[index].match(/^\d+\.\s/)
    ) {
      paraLines.push(lines[index]);
      index += 1;
    }
    if (paraLines.length > 0) {
      result.push(`<p>${paraLines.map(renderInline).join("<br>")}</p>`);
    }
  }

  return result.join("\n");
}
