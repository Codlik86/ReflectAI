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
  has_auto_renew?: boolean | null;  // автопродление (если есть подписка)
};

// Основная функция: возвращает снапшот доступа
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

/** Шорткат: только булевый признак */
export async function hasAccess(): Promise<boolean> {
  try {
    const s = await checkAccess();
    return !!s.ok;
  } catch {
    return false;
  }
}

/**
 * Совместимая обёртка под старый импорт.
 * Если доступа нет — по возможности редиректит на "/paywall".
 *
 * Примеры совместимости:
 *   await ensureAccess(navigate)                       // передан navigate из react-router
 *   await ensureAccess({ navigate })                  // объект с navigate
 *   await ensureAccess({ onDenied: () => nav('/paywall') })
 */
export async function ensureAccess(
  navigateOrOpts?: any
): Promise<boolean> {
  const ok = await hasAccess();
  if (ok) return true;

  // попытка редиректа, если передали navigate
  try {
    // вариант 1: функция navigate
    if (typeof navigateOrOpts === "function") {
      navigateOrOpts("/paywall", { replace: true });
    }
    // вариант 2: объект с полем navigate
    else if (
      navigateOrOpts &&
      typeof navigateOrOpts.navigate === "function"
    ) {
      navigateOrOpts.navigate("/paywall", { replace: true });
    }
    // вариант 3: кастомный колбэк
    else if (
      navigateOrOpts &&
      typeof navigateOrOpts.onDenied === "function"
    ) {
      navigateOrOpts.onDenied();
    }
  } catch {
    // молча
  }

  return false;
}
