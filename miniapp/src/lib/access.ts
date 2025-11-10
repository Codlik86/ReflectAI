// src/lib/access.ts
// Единая точка: получить tg_user_id и проверить/изменить доступ у бэкенда

export type AccessApiOut = {
  ok: boolean;                     // есть доступ (триал/подписка)
  until?: string | null;           // до какого времени действует доступ (UTC ISO)
  has_auto_renew?: boolean | null; // автопродление включено
};

// Базовый URL API: Vercel → укажи в .env.local VITE_API_BASE=https://selflect.onrender.com
const API_BASE =
  (import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ||
    window.localStorage.getItem("VITE_API_BASE")?.replace(/\/$/, "") ||
    "");

// Аккуратно достанем Telegram user id
export function getTelegramUserId(): number | null {
  try {
    // 1) нативно из Telegram Mini App
    // @ts-ignore
    const wa = window?.Telegram?.WebApp;
    const id = wa?.initDataUnsafe?.user?.id;
    if (typeof id === "number" && Number.isFinite(id)) return id;

    // 2) отладочный путь (локалка): можно положить в localStorage
    const fromLS = window.localStorage.getItem("DEBUG_TG_ID");
    if (fromLS && /^\d+$/.test(fromLS)) return Number(fromLS);

    return null;
  } catch {
    return null;
  }
}

// Фактическая проверка доступа
export async function getAccessStatus(): Promise<AccessApiOut> {
  if (!API_BASE) {
    // чтобы не падать совсем — вернём «нет доступа» с подсказкой
    return { ok: false };
  }

  const tg_user_id = getTelegramUserId();
  if (!tg_user_id) {
    // без id Telegram мы не сможем проверить доступ
    return { ok: false };
  }

  const url = `${API_BASE}/api/access/check`;

  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // cookies не нужны для этого запроса
    body: JSON.stringify({ tg_user_id }),
  });

  if (!r.ok) {
    // 4xx/5xx — считаем, что доступа нет
    return { ok: false };
  }

  const data = (await r.json()) as {
    ok: boolean;
    until?: string | null;
    has_auto_renew?: boolean | null;
  };

  // бэкенд возвращает ровно эти поля → просто прокинем их наверх
  return {
    ok: !!data.ok,
    until: data.until ?? null,
    has_auto_renew: data.has_auto_renew ?? null,
  };
}

// Принять правила (пишем отметку на сервере)
export async function acceptPolicy(): Promise<void> {
  const tg_user_id = getTelegramUserId();
  if (!API_BASE || !tg_user_id) throw new Error("no_api_or_user");

  const r = await fetch(`${API_BASE}/api/access/accept-policy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tg_user_id }),
  });

  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || "accept_failed");
  }
}
