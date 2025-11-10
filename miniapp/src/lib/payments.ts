// src/lib/payments.ts
// Хелперы для Paywall: статус доступа и (опц.) создание платежа

import { getTelegramUserId } from "./access";

export type PayStatus = {
  ok: boolean;
  error?: string;

  // триал
  trial_started?: boolean;
  trial_until?: string | null;

  // подписка
  active?: boolean; // есть активная подписка
  plan?: "week" | "month" | "quarter" | "year" | null;
  until?: string | null; // дата окончания (подписки или триала — смотря какая ветка выбрана)
};

// API base из ENV или из localStorage (как в access.ts)
const API_BASE =
  (import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ||
    window.localStorage.getItem("VITE_API_BASE")?.replace(/\/$/, "") ||
    "");

/**
 * 1) Получить статус оплаты/доступа.
 * Бэкенд может вернуть один из форматов:
 *  a) { status: "active"|"trial"|..., until, plan, is_auto_renew }
 *  b) { has_access, plan, trial_started_at, subscription_until }
 */
export async function fetchPayStatus(): Promise<PayStatus> {
  if (!API_BASE) return { ok: false, error: "NO_API_BASE" };

  const tg = getTelegramUserId();
  const url = tg
    ? `${API_BASE}/api/payments/status?tg_user_id=${tg}`
    : `${API_BASE}/api/payments/status`;

  let r: Response;
  try {
    r = await fetch(url, { credentials: "omit" });
  } catch (e: any) {
    return { ok: false, error: e?.message || "NETWORK_ERROR" };
  }

  if (!r.ok) {
    return { ok: false, error: `${r.status} ${r.statusText}` };
  }

  const data = (await r.json()) as any;

  // ----- Вариант (a): status/ until -----
  if (typeof data?.status === "string") {
    const st = (data.status as string).toLowerCase();
    const until = data?.until ?? null;
    const plan = (data?.plan ?? null) as PayStatus["plan"];

    if (st === "active") {
      return { ok: true, active: true, plan, until };
    }
    if (st === "trial") {
      return { ok: true, active: false, plan, until, trial_started: true, trial_until: until };
    }
    // любой иной статус — считаем доступа нет
    return { ok: true, active: false, plan: plan ?? null, until: until ?? null, trial_started: false, trial_until: null };
  }

  // ----- Вариант (b): has_access / trial_started_at / subscription_until -----
  const now = Date.now();
  const subUntilIso = data?.subscription_until ?? null;
  const subUntilMs = subUntilIso ? new Date(subUntilIso).getTime() : 0;
  const subActive = !!subUntilMs && subUntilMs > now;

  const trialStartedAt = data?.trial_started_at ?? null;
  // В проекте триал = 5 дней от trial_started_at (если бек не шлёт trial_expires_at)
  const trialExpiresIso =
    data?.trial_expires_at ??
    (trialStartedAt ? new Date(new Date(trialStartedAt).getTime() + 5 * 24 * 60 * 60 * 1000).toISOString() : null);
  const trialActive = Boolean(trialStartedAt) && trialExpiresIso && new Date(trialExpiresIso).getTime() > now;

  if (subActive) {
    return {
      ok: true,
      active: true,
      plan: (data?.plan ?? null) as PayStatus["plan"],
      until: subUntilIso,
      trial_started: Boolean(trialStartedAt),
      trial_until: trialExpiresIso ?? null,
    };
  }

  if (trialActive) {
    return {
      ok: true,
      active: false,
      plan: (data?.plan ?? null) as PayStatus["plan"],
      until: trialExpiresIso,
      trial_started: true,
      trial_until: trialExpiresIso,
    };
  }

  // по умолчанию — доступа нет
  return {
    ok: true,
    active: false,
    plan: (data?.plan ?? null) as PayStatus["plan"],
    until: null,
    trial_started: Boolean(trialStartedAt),
    trial_until: trialExpiresIso ?? null,
  };
}

/** 2) Создать платёж и вернуть redirect URL на YooKassa (пока не используем из миниаппа) */
export async function createPaymentLink(plan: "week" | "month" | "quarter" | "year"): Promise<string> {
  if (!API_BASE) throw new Error("NO_API_BASE");

  const tg = getTelegramUserId();
  const return_url = `${window.location.origin}/paywall?status=ok`;

  const r = await fetch(`${API_BASE}/api/payments/yookassa/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify({
      user_id: tg, // бэк примет и users.id, и tg_id (по твоей реализации)
      plan,
      return_url,
      description: `Помни — ${plan}`,
    }),
  });

  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || "Не удалось создать платёж.");
  }

  const data = (await r.json()) as { payment_id?: string; confirmation_url?: string };
  if (!data?.confirmation_url) throw new Error("Некорректный ответ YooKassa.");
  return data.confirmation_url;
}
