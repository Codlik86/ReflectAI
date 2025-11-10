// src/lib/access.ts
// Единая точка правды для миниаппа: идентификатор пользователя, доступ и принятие правил.

export type AccessSnapshot = {
  has_access: boolean;
  plan?: "week" | "month" | "quarter" | "year" | null;
  trial_started_at?: string | null;
  subscription_until?: string | null;
  // иногда бек может добавить trial_expires_at — учитываем, если внезапно появится
  trial_expires_at?: string | null;
};

export type AccessComputed = {
  hasAccess: boolean; // подписка ИЛИ активный триал
  reason: "subscription" | "trial" | "none";
  trialStartedAt?: string | null;
  trialUntil?: string | null;        // started_at + 5д (или trial_expires_at, если бек отдаст)
  subscriptionUntil?: string | null; // дата окончания подписки
};

const API_BASE =
  (import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ||
    window.localStorage.getItem("VITE_API_BASE")?.replace(/\/$/, "") ||
    "");

// ===== Telegram user id (из WebApp, иначе DEBUG_TG_ID из localStorage для локалки) =====
export function getTelegramUserId(): number | null {
  try {
    // @ts-ignore
    const wa = window?.Telegram?.WebApp;
    const id = wa?.initDataUnsafe?.user?.id;
    if (typeof id === "number" && Number.isFinite(id)) return id;

    const fromLS = window.localStorage.getItem("DEBUG_TG_ID");
    if (fromLS && /^\d+$/.test(fromLS)) return Number(fromLS);
    return null;
  } catch {
    return null;
  }
}

// ===== Служебка =====
const addDaysISO = (iso: string, days: number) => {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString();
};

// ===== Снапшот доступа из бэка (используется guard.ts) =====
export async function getAccessStatus(): Promise<AccessSnapshot & { ok: boolean }> {
  const tg_id = getTelegramUserId();
  if (!API_BASE || !tg_id) return { ok: false, has_access: false };

  // В access.py эндпоинт ждёт tg_id (а не tg_user_id!)
  const r = await fetch(`${API_BASE}/api/access/status?tg_id=${tg_id}`, { credentials: "omit" });
  if (!r.ok) return { ok: false, has_access: false };

  const raw = (await r.json()) as any;

  // нормализуем поля
  const out: AccessSnapshot = {
    has_access: Boolean(raw?.has_access),
    plan: (raw?.plan ?? null) as any,
    trial_started_at: raw?.trial_started_at ?? null,
    subscription_until: raw?.subscription_until ?? null,
    trial_expires_at: raw?.trial_expires_at ?? null,
  };
  return { ok: true, ...out };
}

// ===== Главный хелпер для UI: посчитать доступ с учётом 5-дневного триала =====
export async function computeAccess(): Promise<AccessComputed> {
  const snap = await getAccessStatus();
  if (!snap.ok) return { hasAccess: false, reason: "none" };

  const now = Date.now();

  // подписка
  const subUntilIso = snap.subscription_until ?? null;
  const subUntilMs = subUntilIso ? new Date(subUntilIso).getTime() : 0;
  const subActive = !!subUntilMs && subUntilMs > now;

  // триал: приоритет готового trial_expires_at, иначе считаем started_at + 5д.
  const trialStartedAt = snap.trial_started_at ?? null;
  const trialUntilIso =
    (snap.trial_expires_at as string | null) ??
    (trialStartedAt ? addDaysISO(trialStartedAt, 5) : null);
  const trialUntilMs = trialUntilIso ? new Date(trialUntilIso).getTime() : 0;
  const trialActive = Boolean(trialStartedAt) && trialUntilMs > now;

  if (subActive) {
    return {
      hasAccess: true,
      reason: "subscription",
      subscriptionUntil: subUntilIso,
      trialStartedAt,
      trialUntil: trialUntilIso,
    };
  }
  if (trialActive) {
    return {
      hasAccess: true,
      reason: "trial",
      subscriptionUntil: subUntilIso,
      trialStartedAt,
      trialUntil: trialUntilIso,
    };
  }
  return {
    hasAccess: false,
    reason: "none",
    subscriptionUntil: subUntilIso,
    trialStartedAt,
    trialUntil: trialUntilIso,
  };
}

// ===== Принятие правил из мини-аппа =====
// Пытаемся вызвать POST /api/access/accept { tg_user_id }.
// Если на бэке нет этого эндпоинта (404) — кидаем специальную ошибку "NO_ACCEPT_ENDPOINT".
export async function acceptPolicy(): Promise<void> {
  const tg_id = getTelegramUserId();
  if (!API_BASE || !tg_id) throw new Error("NO_USER_OR_API_BASE");

  const r = await fetch(`${API_BASE}/api/access/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify({ tg_user_id: tg_id }),
  });

  if (r.status === 404) {
    // Фронт может обработать это и сделать fallback: открыть бота на шаге правил.
    throw new Error("NO_ACCEPT_ENDPOINT");
  }

  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || "ACCEPT_FAILED");
  }
}
