// src/lib/payments.ts
// Хелперы для Paywall/оплаты: статус доступа и создание платежа

import { getTelegramUserId } from "./telegram";

export type PayStatus = {
  ok: boolean;
  error?: string;

  // нормализованный доступ
  has_access?: boolean; // подписка ИЛИ активный триал
  status?: "active" | "trial" | "none" | null;
  plan?: "week" | "month" | "quarter" | "year" | null;
  until?: string | null; // дедлайн подписки или триала (унифицированный)
  is_auto_renew?: boolean | null;

  // продуктовые флаги
  needs_policy?: boolean | null; // НОВОЕ: требуются ли принятые правила

  // обратная совместимость с очень старым бэком
  active?: boolean; // есть активная подписка (устар.)
  trial_started?: boolean;
  trial_until?: string | null;
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

const nowMs = () => Date.now();
const isFuture = (iso?: string | null) => {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) && t > nowMs();
};

// ==== 1) Получить статус оплаты/доступа =====================================

/**
 * Пытаемся новый канон: /api/payments/status?tg_user_id=...
 * Поддерживаем и старые форматы ответа.
 */
export async function fetchPayStatus(): Promise<PayStatus> {
  const tg = getTelegramUserId();

  try {
    const u = new URL(apiUrl("/api/payments/status"), window.location.origin);
    if (tg) u.searchParams.set("tg_user_id", String(tg));

    const r = await fetch(u.toString(), { credentials: "omit" });
    if (!r.ok) {
      return { ok: false, error: `${r.status} ${r.statusText}` };
    }

    const data = (await r.json()) as any;

    // ----- Канонический формат: { has_access, status, until, plan, is_auto_renew, needs_policy, ... }
    if (
      "status" in data ||
      "has_access" in data ||
      "until" in data ||
      "plan" in data ||
      "is_auto_renew" in data ||
      "needs_policy" in data
    ) {
      const status = (data?.status ?? null) as PayStatus["status"];
      const until = (data?.until ?? null) as string | null;
      const plan = (data?.plan ?? null) as PayStatus["plan"];
      const needs_policy =
        typeof data?.needs_policy === "boolean" ? (data.needs_policy as boolean) : null;

      // доступ по флагу с бэка ИЛИ по (status+until)
      const activeByStatus = status === "active" && isFuture(until);
      const trialByStatus = status === "trial" && isFuture(until);
      const has_access =
        typeof data?.has_access === "boolean"
          ? Boolean(data.has_access)
          : activeByStatus || trialByStatus;

      return {
        ok: true,
        has_access,
        status: status ?? null,
        plan,
        until,
        is_auto_renew:
          typeof data?.is_auto_renew === "boolean" ? (data.is_auto_renew as boolean) : null,
        needs_policy,
        // совместимость со старым потреблением
        active: status === "active" ? has_access : false,
        trial_started: status === "trial" ? has_access : Boolean(data?.trial_started_at),
        trial_until: status === "trial" ? until : (data?.trial_expires_at ?? null),
      };
    }

    // ----- Старый формат: { has_access, plan, trial_started_at, trial_expires_at?, subscription_until }
    const subUntilIso = (data?.subscription_until ?? null) as string | null;
    const subActive = isFuture(subUntilIso);

    const trialStartedAt = (data?.trial_started_at ?? null) as string | null;
    const trialExpiresIso = (data?.trial_expires_at ??
      (trialStartedAt ? addDaysISO(trialStartedAt, 5) : null)) as string | null;
    const trialActive = Boolean(trialStartedAt) && isFuture(trialExpiresIso);

    if (subActive) {
      return {
        ok: true,
        has_access: true,
        status: "active",
        plan: (data?.plan ?? null) as PayStatus["plan"],
        until: subUntilIso,
        is_auto_renew:
          typeof data?.is_auto_renew === "boolean" ? (data.is_auto_renew as boolean) : null,
        needs_policy: null,
        active: true,
        trial_started: Boolean(trialStartedAt),
        trial_until: trialExpiresIso ?? null,
      };
    }

    if (trialActive) {
      return {
        ok: true,
        has_access: true,
        status: "trial",
        plan: (data?.plan ?? null) as PayStatus["plan"],
        until: trialExpiresIso,
        is_auto_renew:
          typeof data?.is_auto_renew === "boolean" ? (data.is_auto_renew as boolean) : null,
        needs_policy: null,
        active: false,
        trial_started: true,
        trial_until: trialExpiresIso,
      };
    }

    // по умолчанию — доступа нет
    return {
      ok: true,
      has_access: false,
      status: "none",
      plan: (data?.plan ?? null) as PayStatus["plan"],
      until: null,
      is_auto_renew:
        typeof data?.is_auto_renew === "boolean" ? (data.is_auto_renew as boolean) : null,
      needs_policy: null,
      active: false,
      trial_started: Boolean(trialStartedAt),
      trial_until: trialExpiresIso ?? null,
    };
  } catch (e: any) {
    return { ok: false, error: e?.message || "NETWORK_ERROR" };
  }
}

// ==== 2) Создать платёж и вернуть redirect URL на YooKassa ==================

/**
 * Соответствует нашему бэку:
 * POST /api/payments/yookassa/create
 * body: { user_id, plan, description?, return_url?, ad? }
 */
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
      user_id: tg,                   // ВАЖНО: наш бэк ждёт user_id (можно tg_id)
      plan,
      return_url,
      description: `Помни — ${plan}`,
      // ad: можно передать метку кампании при необходимости
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
