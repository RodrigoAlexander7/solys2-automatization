import { render, titleOf } from './docs-render';

export type Doc = { slug: string; title: string; html: string };

// Se compilan en el build desde docs/fisicos/: la documentación funciona sin internet
// y es la misma que se lee en el repositorio.
const files = import.meta.glob('../../../docs/fisicos/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

export const docs: Doc[] = Object.entries(files)
  .map(([path, text]) => {
    const slug = path.split('/').pop()!.replace(/\.md$/, '');
    return { slug, title: titleOf(text, slug), html: render(text) };
  })
  .sort((a, b) => a.slug.localeCompare(b.slug));
