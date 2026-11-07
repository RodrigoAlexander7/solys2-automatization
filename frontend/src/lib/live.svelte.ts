import { get, type ApiError, type Live } from './api';

/** Estado en vivo compartido por el layout y las páginas: una sola consulta periódica. */
export const live = $state<{ data: Live | null; error: ApiError | null; updated: number }>({
  data: null, error: null, updated: 0
});

let timer: ReturnType<typeof setTimeout> | undefined;
let users = 0;
let running = false;

async function poll(intervalMs: number) {
  if (!running) return;
  const result = await get<Live>('/api/live');
  if (!running) return;
  if (result.ok) { live.data = result.value; live.error = null; }
  else live.error = result.error;
  live.updated = Date.now();
  timer = setTimeout(() => void poll(intervalMs), intervalMs);
}

export function refreshLive() {
  clearTimeout(timer);
  if (running) void poll(2000);
}

export function useLive(intervalMs = 2000): () => void {
  users++;
  if (!running) { running = true; void poll(intervalMs); }
  return () => {
    users = Math.max(0, users - 1);
    if (!users) { running = false; clearTimeout(timer); }
  };
}

const AUTHOR_KEY = 'radiometria.autor';

export function savedAuthor(): string {
  try { return localStorage.getItem(AUTHOR_KEY) ?? ''; } catch { return ''; }
}

export function rememberAuthor(author: string) {
  try { localStorage.setItem(AUTHOR_KEY, author); } catch { /* almacenamiento no disponible */ }
}
