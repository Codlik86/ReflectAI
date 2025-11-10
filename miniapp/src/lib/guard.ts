// src/lib/guard.ts
// Единая проверка доступа для Mini App (синхронно с /api/access/check)

import { getTelegramUserId } from "./telegram";

const API_BASE =
  (import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ||
    window.localStorage.getItem("VITE_API_BASE")?.replace(/\/$/, "") ||
    "");

// Ответ бэка /api/access/check
export type AccessSnapshot = {
  ok: boolean;                      // есть доступ (подписка ИЛИ активный триал)
  until?: string | null;            // дата окончания доступа (если есть)
  has_auto_renew?: boolean | null;  // автопродление подписки (если есть подписка)
};

// Основная функция: true/false + полезные поля
export async function checkAccess(): Promise<AccessSnapshot> {
  const tg = getTelegramUserId();
  if (!API_BASE || !tg) return { ok: false };

  const r = await fetch(`${API_BASE}/api/access/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify({ tg_user_id: tg }),
  });

  if (!r.ok) return { ok: false };

  const data = (await r.json()) as {
    ok: boolean;
    until?: string | null;
    has_auto_renew?: boolean | null;
  };

  return {
    ok: !!data.ok,
    until: data.until ?? null,
    has_auto_renew: data.has_auto_renew ?? null,
  };
}

/** Удобный шорткат только для булевого признака */
export async function hasAccess(): Promise<boolean> {
  try {
    const s = await checkAccess();
    return !!s.ok;
  } catch {
    return false;
  }
}
