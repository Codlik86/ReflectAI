// src/lib/payments.ts
// Хелперы для Paywall: статус доступа и создание платежа (YooKassa)

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

export async function fetchPayStatus(): Promise<PayStatus> {
  const res = await fetch(`${API_BASE}/api/payments/status`, {
    credentials: "include",
  });
  return res.json();
}

/** Возвращает redirect URL на YooKassa (или кидает ошибку) */
export async function createPaymentLink(
  plan: "month" | "year"
): Promise<string> {
  const res = await fetch(`${API_BASE}/api/payments/create`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  const data = (await res.json()) as { url?: string; error?: string };
  if (!data.url) throw new Error(data.error || "Не удалось создать платёж.");
  return data.url;
}
