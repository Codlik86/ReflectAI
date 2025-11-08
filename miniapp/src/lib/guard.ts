// src/lib/guard.ts
// Централизованный гейт для упражнений/медитаций

type AccessStatus = {
  ok: boolean;
  // доступ открыт, если активен триал или есть подписка
  has_access: boolean;
  // справочная инфа
  trial_started?: boolean;
  trial_until?: string | null;
  sub_active?: boolean;
  sub_until?: string | null;
};

const API_BASE = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "";

// эндпойнты на бэке (поменяй, если у тебя другие пути)
const API_ACCESS = `${API_BASE}/api/access/status`;      // GET
const API_TRIAL  = `${API_BASE}/api/trial/start`;         // POST (автостарт отложенного триала)
const API_LOG    = `${API_BASE}/api/events/miniapp`;      // POST опционально (трек)

let cache: { at: number; data: AccessStatus } | null = null;
const STALE_MS = 20_000; // 20 секунд кэша, чтобы не долбить бэк

async function getAccessFresh(): Promise<AccessStatus> {
  const r = await fetch(API_ACCESS, { credentials: "include" });
  if (!r.ok) throw new Error(`ACCESS ${r.status}`);
  return (await r.json()) as AccessStatus;
}

/**
 * Безопасный доступ со слабым кэшем.
 * force=true — игнорировать кэш (например, после старта триала).
 */
export async function getAccess(force = false): Promise<AccessStatus> {
  const now = Date.now();
  if (!force && cache && now - cache.at < STALE_MS) return cache.data;

  const data = await getAccessFresh();
  cache = { at: now, data };
  return data;
}

/**
 * Пытаемся обеспечить доступ:
 * 1) Проверяем статус
 * 2) Если доступа нет и триал ещё не стартовал — запускаем триал
 * 3) Перечитываем статус и возвращаем результат
 */
export async function ensureAccess(autoStartTrial = true): Promise<AccessStatus> {
  let s = await getAccess();
  if (s.has_access) return s;

  if (autoStartTrial && !s.trial_started) {
    await startTrial();
    s = await getAccess(true);
  }
  return s;
}

export async function startTrial(): Promise<void> {
  // Запускаем триал и не падаем, даже если бэк вернул 409/400
  try {
    await fetch(API_TRIAL, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "miniapp-first-action" }),
    });
  } catch { /* noop */ }
}

/**
 * Универсальный хелпер для действий, требующих доступа.
 * Если доступа нет — редиректит на пейволл.
 */
export async function requireAccessOrPaywall(
  navigate: (to: string) => void,
  opts?: { autoStartTrial?: boolean; returnTo?: string; event?: string }
): Promise<boolean> {
  const s = await ensureAccess(opts?.autoStartTrial ?? true);
  if (s.has_access) {
    if (opts?.event) track(opts.event);
    return true;
  }
  // отправляем на пейволл, запоминаем куда вернуться
  const ret = encodeURIComponent(opts?.returnTo || location.pathname + location.search);
  navigate(`/paywall?from=${ret}`);
  return false;
}

// Опциональный мягкий лог (можно вырубить)
export function track(event: string, extra?: Record<string, any>) {
  try {
    fetch(API_LOG, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, ...extra }),
    });
  } catch { /* noop */ }
}
