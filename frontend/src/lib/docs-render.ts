import { marked } from 'marked';

/** Markdown propio del repositorio → HTML, con enlaces entre documentos convertidos a anclas. */
export function render(markdown: string): string {
  const html = marked.parse(markdown, { async: false, gfm: true }) as string;
  return html
    .replace(/href="(\d{2}-[\w-]+)\.md(?:#[^"]*)?"/g, 'href="#$1"')
    .replace(/<a href="(https?:[^"]+)"/g, '<a href="$1" target="_blank" rel="noopener"');
}

export function titleOf(markdown: string, fallback: string): string {
  return markdown.match(/^#\s+(.+)$/m)?.[1].trim() ?? fallback;
}
