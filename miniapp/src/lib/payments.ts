// src/lib/payments.ts
// Хелперы для Paywall: статус доступа и создание платежа (YooKassa)
import { getTelegramUserId } from "./telegram";

export type PayStatus = {
  ok: boolean;
  error?: string;

  // триал
  trial_started?: boolean;
  trial_until?: string | null;

  // подписка
  active?: boolean;
  plan?: "week" | "month" | "quarter" | "year" | null;
  until?: string | null;
};

const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

// 1) Получить статус (учитываем tg_user_id)
export async function fetchPayStatus(): Promise<PayStatus> {
  const tg = getTelegramUserId();
  const url = tg
    ? `${API_BASE}/api/payments/status?tg_user_id=${tg}`
    : `${API_BASE}/api/payments/status`; // на всякий, но бэк вернёт 400

  const r = await fetch(url, { credentials: "omit" });
  if (!r.ok) {
    return { ok: false, error: `${r.status} ${r.statusText}` };
  }
  // маппинг ответа бэка -> в поля PayStatus, с поддержкой триала
  const data = await r.json() as {
    has_access: boolean;
    plan?: "week" | "month" | "quarter" | "year" | null;
    status?: string | null;   // "active" | "trial" | ...
    until?: string | null;
    is_auto_renew?: boolean | null;
  };

  return {
    ok: true,
    active: data.status === "active",
    plan: data.plan ?? null,
    until: data.until ?? null,
    trial_started: data.status === "trial",
    trial_until: data.status === "trial" ? data.until ?? null : null,
  };
}

/** 2) Создать платёж и вернуть redirect URL на YooKassa */
export async function createPaymentLink(
  plan: "month" | "year"
): Promise<string> {
  const tg = getTelegramUserId();
  // куда вернуться после редиректа из YooKassa
  const return_url = `${window.location.origin}/paywall?status=ok`;

  const r = await fetch(`${API_BASE}/api/payments/yookassa/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify({
      user_id: tg,          // бэк примет и users.id, и tg_id
      plan,
      return_url,
      description: `Помни — ${plan}`,
    }),
  });

  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || "Не удалось создать платёж.");
  }
  const data = (await r.json()) as { payment_id: string; confirmation_url: string };
  if (!data.confirmation_url) throw new Error("Некорректный ответ YooKassa.");
  return data.confirmation_url;
}
