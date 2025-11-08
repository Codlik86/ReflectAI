// src/lib/access.ts
import { baseUrl, fetchJson } from "./api";

export type AccessStatus = {
  has_access: boolean;
  plan?: string | null;
  trial_started_at?: string | null;
  subscription_until?: string | null;
};

export async function getAccessStatus(): Promise<AccessStatus> {
  return fetchJson<AccessStatus>(`${baseUrl()}/api/access/status`);
}

// опционально: если на бэке есть ручка создания ссылки оплаты
export type CreatePaymentResp = {
  payment_url: string;  // куда редиректить
};

export async function createMiniappPayment(plan: "week" | "month" | "quarter" | "year" = "month") {
  return fetchJson<CreatePaymentResp>(`${baseUrl()}/api/payments/create-miniapp`, {
    method: "POST",
    body: JSON.stringify({ plan }),
  });
}
