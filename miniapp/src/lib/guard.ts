// src/lib/guard.ts
// Централизованный гейт для упражнений/медитаций/переходов

import { getAccessStatus } from "./access";

type AccessStatus = {
  ok: boolean;
  has_access: boolean;              // дублируем для удобства в UI
  trial_started?: boolean;          // (опц.) если решишь расширить /check
  trial_until?: string | null;      // (опц.)
  sub_active?: boolean;             // (опц.)
  sub_until?: string | null;        // (опц.)
  until?: string | null;            // текущая дата окончания доступа (из API)
  has_auto_renew?: boolean | null;  // автопродление подписки
};

// Базовые пути (если где-то логгируешь события — оставил как заготовку)
const API_BASE = import.meta.env.VITE_API_BASE?.replace(/\/$/, "") || "";
const API_LOG = API_BASE ? `${API_BASE}/api/miniapp/log` : "";

// Универсальная функция: проверяем доступ.
// Параметр autoStartTrial оставил на будущее (когда подключим эндпоинт автозапуска триала),
// сейчас он не используется — /api/access/check только читает статус.
export async function ensureAccess(_autoStartTrial?: boolean): Promise<AccessStatus> {
  const res = await getAccessStatus();

  // Маппинг в формат, который уже использует твой UI
  const status: AccessStatus = {
    ok: !!res.ok,
    has_access: !!res.ok,
    until: res.until ?? null,
    has_auto_renew: res.has_auto_renew ?? null,
  };

  return status;
}

// Опциональный мягкий лог (можно вырубить/переименовать на бекенде)
export function track(event: string, extra?: Record<string, any>) {
  if (!API_LOG) return;
  try {
    fetch(API_LOG, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, ...extra }),
    }).catch(() => {});
  } catch {
    /* noop */
  }
}
