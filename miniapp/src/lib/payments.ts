// src/lib/payments.ts
// Хелперы для Paywall: статус доступа и (опц.) создание платежа

import { getTelegramUserId } from "./telegram";

export type PayStatus = {
  ok: boolean;
  error?: string;

  // триал
  trial_started?: boolean;
  trial_until?: string | null;

  // подписка
  active?: boolean; // есть активная подписка
  plan?: "week" | "month" | "quarter" | "year" | null;
  until?: string | null; // дата окончания (подписки или триала — в зависимости от ветки)
};

// ==== API base (как в guard.ts / access.ts) =================================

function normalizeBase(s?: string | null) {
  if (!s) return "";
  return s.replace(/\/+$/, "");
}

const API_BASE =
  normalizeBase((import.meta as any)?.env?.VITE_API_BASE) ||
  normalizeBase(window.localStorage.getItem("VITE_API_BASE")) ||
  "";

const apiUrl = (path: string) => (API_BASE ? `${API_BASE}${path}` : path);

// ==== Утилиты ===============================================================

const addDaysISO = (iso: string, days: number) => {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString();
};

const isFuture = (iso?: string | null) => {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) && t > Date.now();
};

// ==== 1) Получить статус оплаты/доступа =====================================

/**
 * Бэкенд может вернуть один из форматов:
 *  a) { status: "active"|"trial"|"none", until, plan, is_auto_renew, has_access? }
 *  b) { has_access, plan, trial_started_at, trial_expires_at?, subscription_until }
 */
export async function fetchPayStatus(): Promise<PayStatus> {
  const tg = getTelegramUserId();

  try {
    // Пытаемся новый канон: /api/payments/status?tg_user_id=...
    const u = new URL(apiUrl("/api/payments/status"), window.location.origin);
    if (tg) u.searchParams.set("tg_user_id", String(tg));

    const r = await fetch(u.toString(), { credentials: "omit" });
    if (!r.ok) {
      return { ok: false, error: `${r.status} ${r.statusText}` };
    }

    const data = (await r.json()) as any;

    // ----- Вариант (a): status/until -----
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
      return {
        ok: true,
        active: false,
        plan: plan ?? null,
        until: until ?? null,
        trial_started: false,
        trial_until: null,
      };
    }

    // ----- Вариант (b): has_access / trial_started_at / subscription_until -----
    const subUntilIso = data?.subscription_until ?? null;
    const subActive = isFuture(subUntilIso);

    const trialStartedAt = data?.trial_started_at ?? null;
    const trialExpiresIso = data?.trial_expires_at ?? (trialStartedAt ? addDaysISO(trialStartedAt, 5) : null);
    const trialActive = Boolean(trialStartedAt) && isFuture(trialExpiresIso);

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
  } catch (e: any) {
    return { ok: false, error: e?.message || "NETWORK_ERROR" };
  }
}

// ==== 2) Создать платёж и вернуть redirect URL на YooKassa ==================

export async function createPaymentLink(
  plan: "week" | "month" | "quarter" | "year"
): Promise<string> {
  const tg = getTelegramUserId();
  if (!tg) throw new Error("NO_USER");

  const return_url = `${window.location.origin}/paywall?status=ok`;

  const r = await fetch(apiUrl("/api/payments/yookassa/create"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify({
      // шлём оба поля, чтобы бэк однозначно нашёл пользователя
      tg_user_id: tg,
      tg_id: tg,
      plan,
      return_url,
      description: `Помни — ${plan}`,
      source: "miniapp",
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
