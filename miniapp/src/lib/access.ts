// src/lib/access.ts
// Единая точка правды для миниаппа

export type AccessComputed = {
  hasAccess: boolean;           // подписка ИЛИ активный триал
  reason: "subscription" | "trial" | "none";
  trialStartedAt?: string | null;
  trialUntil?: string | null;   // вычисляем локально = started + 5д
  subscriptionUntil?: string | null;
};

const API_BASE =
  (import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ||
    window.localStorage.getItem("VITE_API_BASE")?.replace(/\/$/, "") ||
    "");

// Telegram user id (из webapp или DEBUG_TG_ID для локалки)
export function getTelegramUserId(): number | null {
  try {
    // @ts-ignore
    const wa = window?.Telegram?.WebApp;
    const id = wa?.initDataUnsafe?.user?.id;
    if (typeof id === "number" && Number.isFinite(id)) return id;
    const fromLS = window.localStorage.getItem("DEBUG_TG_ID");
    if (fromLS && /^\d+$/.test(fromLS)) return Number(fromLS);
    return null;
  } catch { return null; }
}

// Служебка
const addDays = (iso: string, days: number) => {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString();
};

// Главный вызов: берём снапшот и САМИ считаем доступ
export async function computeAccess(): Promise<AccessComputed> {
  const tg = getTelegramUserId();
  if (!API_BASE || !tg) return { hasAccess: false, reason: "none" };

  // Твой /api/access/status отдаёт:
  // { has_access, plan, trial_started_at, subscription_until }
  const r = await fetch(`${API_BASE}/api/access/status?tg_id=${tg}`, { credentials: "omit" });
  if (!r.ok) return { hasAccess: false, reason: "none" };
  const s = await r.json() as {
    has_access: boolean;
    trial_started_at?: string | null;
    subscription_until?: string | null;
  };

  const now = Date.now();
  const subUntilMs = s.subscription_until ? new Date(s.subscription_until).getTime() : 0;
  const subActive  = !!subUntilMs && subUntilMs > now;

  // В твоём проекте триал = 5 дней от trial_started_at
  const trialStarted = !!s.trial_started_at;
  const trialUntilIso = trialStarted ? addDays(s.trial_started_at as string, 5) : null;
  const trialUntilMs  = trialUntilIso ? new Date(trialUntilIso).getTime() : 0;
  const trialActive   = trialStarted && trialUntilMs > now;

  if (subActive) {
    return {
      hasAccess: true,
      reason: "subscription",
      subscriptionUntil: s.subscription_until ?? null,
      trialStartedAt: s.trial_started_at ?? null,
      trialUntil: trialUntilIso,
    };
  }
  if (trialActive) {
    return {
      hasAccess: true,
      reason: "trial",
      subscriptionUntil: s.subscription_until ?? null,
      trialStartedAt: s.trial_started_at ?? null,
      trialUntil: trialUntilIso,
    };
  }
  return {
    hasAccess: false,
    reason: "none",
    subscriptionUntil: s.subscription_until ?? null,
    trialStartedAt: s.trial_started_at ?? null,
    trialUntil: trialUntilIso,
  };
}

// src/lib/access.ts — добавить безопасную заглушку при желании
export async function acceptPolicy(): Promise<void> {
  // В бэкенде нет соответствующего API.
  // Реальное принятие происходит в боте на onb:agree.
  // Открывай бота и нажимай «Принимаю».
  throw new Error("acceptPolicy API недоступен — открой бота и нажми «Принимаю».");
}