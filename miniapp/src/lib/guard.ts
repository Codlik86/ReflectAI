// src/lib/guard.ts
import { getTelegramUserId } from "./telegram";

type StatusDTO = {
  ok?: boolean;
  status?: "active" | "trial" | "none" | null;
  until?: string | null;
  plan?: "week" | "month" | "quarter" | "year" | null;
};

export type AccessSnapshot = {
  ok: boolean;
  has_access: boolean;
  reason: "subscription" | "trial" | "none";
  until: string | null;
};

const API_BASE =
  (import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ||
    window.localStorage.getItem("VITE_API_BASE")?.replace(/\/$/, "") ||
    "");

const CACHE_KEY = "ACCESS_OK_UNTIL"; // ms timestamp

function nowMs() { return Date.now(); }
function isFuture(iso?: string | null) {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) && t > nowMs();
}

function getCachedOk(): boolean {
  const until = Number(sessionStorage.getItem(CACHE_KEY) || "0");
  return Number.isFinite(until) && until > nowMs();
}
function putCachedOk(ms: number = 60_000) {
  sessionStorage.setItem(CACHE_KEY, String(nowMs() + ms));
}

/** Ждём появления tg_id (в WebApp он иногда появляется с задержкой) */
async function waitTelegramId(maxTries = 5, delayMs = 120): Promise<number | null> {
  for (let i = 0; i < maxTries; i++) {
    const id = getTelegramUserId();
    if (id) return id;
    await new Promise(r => setTimeout(r, delayMs));
  }
  return getTelegramUserId();
}

/** Единый гард. Возвращает снимок доступа. */
export async function ensureAccess(autoStartTrial: boolean = true): Promise<AccessSnapshot> {
  // если недавно уже подтверждали доступ — не мигаем экранами
  if (getCachedOk()) {
    return { ok: true, has_access: true, reason: "subscription", until: null };
  }

  const tg = await waitTelegramId();
  if (!API_BASE || !tg) {
    // Сетевой/иниц. сбой — не валим пользователя, дадим Paywall решить
    return { ok: false, has_access: false, reason: "none", until: null };
  }

  const url = new URL(`${API_BASE}/api/payments/status`);
  url.searchParams.set("tg_user_id", String(tg));
  if (autoStartTrial) url.searchParams.set("start_trial", "1");

  let dto: StatusDTO | null = null;
  try {
    const res = await fetch(url.toString(), { credentials: "omit" });
    if (!res.ok) throw new Error(`${res.status}`);
    dto = (await res.json()) as StatusDTO;
  } catch {
    // при сетевой ошибке уважаем кэш, иначе — отрицательно
    if (getCachedOk()) {
      return { ok: true, has_access: true, reason: "subscription", until: null };
    }
    return { ok: false, has_access: false, reason: "none", until: null };
  }

  const subActive = dto?.status === "active" && isFuture(dto?.until);
  const trialActive = dto?.status === "trial" && isFuture(dto?.until);

  if (subActive || trialActive) {
    putCachedOk(); // 60 сек «зелёного» окна, чтобы не было bounce
    return {
      ok: true,
      has_access: true,
      reason: subActive ? "subscription" : "trial",
      until: dto?.until ?? null,
    };
  }

  return { ok: true, has_access: false, reason: "none", until: dto?.until ?? null };
}
