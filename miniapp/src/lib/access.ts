// Единая «точка правды» для мини-аппа: статус доступа и утилиты.
import { getTelegramUserId } from "./telegram";

// ===== Типы (нормализованные) ==============================================
export type AccessSnapshot = {
  ok: boolean;
  has_access: boolean;
  status?: "active" | "trial" | "none" | null;
  until?: string | null;                 // универсальный дедлайн (подписка/триал)
  plan?: "week" | "month" | "quarter" | "year" | null;
  is_auto_renew?: boolean | null;
  needs_policy?: boolean | null;         // НОВОЕ

  // возможные поля для обратной совместимости
  trial_started_at?: string | null;
  trial_expires_at?: string | null;
  subscription_until?: string | null;
};

export type AccessComputed = {
  hasAccess: boolean;                    // подписка ИЛИ активный триал
  reason: "subscription" | "trial" | "none";
  subscriptionUntil?: string | null;
  trialStartedAt?: string | null;
  trialUntil?: string | null;
  needsPolicy?: boolean | null;          // НОВОЕ
};

// ===== API base (как в guard.ts) ===========================================
function normalizeBase(s?: string | null) {
  if (!s) return "";
  return s.replace(/\/+$/, "");
}
const API_BASE =
  normalizeBase((import.meta as any)?.env?.VITE_API_BASE) ||
  normalizeBase(window.localStorage.getItem("VITE_API_BASE")) ||
  "";
const apiUrl = (path: string) => (API_BASE ? `${API_BASE}${path}` : path);

// ===== Служебные утилиты ====================================================
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

// ===== Основной снимок статуса с бэка ======================================
export async function getAccessStatus(): Promise<AccessSnapshot> {
  const tg = getTelegramUserId();
  if (!tg) return { ok: false, has_access: false };

  // 1) payments/status
  try {
    const u = new URL(apiUrl("/api/payments/status"), window.location.origin);
    u.searchParams.set("tg_user_id", String(tg));
    const r = await fetch(u.toString(), { credentials: "omit" });
    if (r.ok) {
      const raw = (await r.json()) as any;
      const snap: AccessSnapshot = {
        ok: true,
        has_access: Boolean(raw?.has_access),
        status: (raw?.status ?? null) as any,
        until: raw?.until ?? null,
        plan: (raw?.plan ?? null) as any,
        is_auto_renew: typeof raw?.is_auto_renew === "boolean" ? raw.is_auto_renew : null,
        needs_policy: typeof raw?.needs_policy === "boolean" ? raw.needs_policy : null,
      };
      if (raw?.trial_started_at) snap.trial_started_at = raw.trial_started_at;
      if (raw?.trial_expires_at) snap.trial_expires_at = raw.trial_expires_at;
      if (raw?.subscription_until) snap.subscription_until = raw.subscription_until;
      return snap;
    }
  } catch {}

  // 2) access/status (fallback/совм.)
  try {
    const u = new URL(apiUrl("/api/access/status"), window.location.origin);
    u.searchParams.set("tg_user_id", String(tg));
    const r = await fetch(u.toString(), { credentials: "omit" });
    if (r.ok) {
      const raw = (await r.json()) as any;

      if ("has_access" in raw || "status" in raw || "until" in raw || "plan" in raw || "is_auto_renew" in raw || "needs_policy" in raw) {
        const snap: AccessSnapshot = {
          ok: true,
          has_access: Boolean(raw?.has_access),
          status: (raw?.status ?? null) as any,
          until: raw?.until ?? null,
          plan: (raw?.plan ?? null) as any,
          is_auto_renew: typeof raw?.is_auto_renew === "boolean" ? raw.is_auto_renew : null,
          needs_policy: typeof raw?.needs_policy === "boolean" ? raw.needs_policy : null,
        };
        if (raw?.trial_started_at) snap.trial_started_at = raw.trial_started_at;
        if (raw?.trial_expires_at) snap.trial_expires_at = raw.trial_expires_at;
        if (raw?.subscription_until) snap.subscription_until = raw.subscription_until;
        return snap;
      }

      // самый старый формат
      const snap: AccessSnapshot = {
        ok: true,
        has_access: Boolean(raw?.has_access),
        trial_started_at: raw?.trial_started_at ?? null,
        subscription_until: raw?.subscription_until ?? null,
        trial_expires_at: raw?.trial_expires_at ?? null,
        status: null,
        until: null,
        plan: (raw?.plan ?? null) as any,
        is_auto_renew: typeof raw?.is_auto_renew === "boolean" ? raw.is_auto_renew : null,
        needs_policy: null,
      };

      const subUntil = snap.subscription_until;
      const trialUntil =
        (snap.trial_expires_at as string | null) ??
        (snap.trial_started_at ? addDaysISO(snap.trial_started_at, 5) : null);

      if (isFuture(subUntil)) {
        snap.status = "active";
        snap.until = subUntil;
      } else if (isFuture(trialUntil)) {
        snap.status = "trial";
        snap.until = trialUntil;
      } else {
        snap.status = "none";
        snap.until = trialUntil || subUntil || null;
      }

      if (!("has_access" in raw)) {
        snap.has_access = snap.status === "active" || snap.status === "trial";
      }

      return snap;
    }
  } catch {}

  return { ok: false, has_access: false };
}

// ===== Главный хелпер для UI ===============================================
export async function computeAccess(): Promise<AccessComputed> {
  const snap = await getAccessStatus();
  if (!snap.ok) return { hasAccess: false, reason: "none", needsPolicy: snap.needs_policy ?? null };

  const until = snap.until ?? snap.subscription_until ?? null;
  const active = snap.status === "active" && isFuture(until);
  const trial = snap.status === "trial" && isFuture(until);
  const has = Boolean(snap.has_access || active || trial);

  let trialUntil: string | null = null;
  if (snap.status === "trial" && until) trialUntil = until;
  else if (snap.trial_expires_at) trialUntil = snap.trial_expires_at;
  else if (snap.trial_started_at) trialUntil = addDaysISO(snap.trial_started_at, 5);

  let subscriptionUntil: string | null = null;
  if (snap.status === "active" && until) subscriptionUntil = until;
  else if (snap.subscription_until) subscriptionUntil = snap.subscription_until;

  if (has) {
    return {
      hasAccess: true,
      reason: active ? "subscription" : "trial",
      subscriptionUntil,
      trialStartedAt: snap.trial_started_at ?? null,
      trialUntil,
      needsPolicy: snap.needs_policy ?? null,
    };
  }

  return {
    hasAccess: false,
    reason: "none",
    subscriptionUntil,
    trialStartedAt: snap.trial_started_at ?? null,
    trialUntil,
    needsPolicy: snap.needs_policy ?? null,
  };
}

// ===== Принятие правил из мини-аппа ========================================
export async function acceptPolicy(): Promise<void> {
  const tg = getTelegramUserId();
  if (!tg) throw new Error("NO_USER");

  const r = await fetch(apiUrl("/api/access/accept"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify({ tg_user_id: tg }),
  });

  if (r.status === 404) throw new Error("NO_ACCEPT_ENDPOINT");
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || "ACCEPT_FAILED");
  }
}
