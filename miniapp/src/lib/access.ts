// src/lib/access.ts
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
  trial_ever?: boolean | null;           // НОВОЕ: был ли когда-либо триал/подписка

  // возможные поля для обратной совместимости/обогащения
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

function mergeSnapshots(base: AccessSnapshot, extra?: any): AccessSnapshot {
  if (!extra) return base;

  // Берём «исторические» поля и мягко дополняем базу
  const trial_started_at = base.trial_started_at ?? extra.trial_started_at ?? null;
  const trial_expires_at = base.trial_expires_at ?? extra.trial_expires_at ?? null;
  const subscription_until = base.subscription_until ?? extra.subscription_until ?? null;

  // trial_ever — если пришёл с бэка, иначе вычисляем по наличию следов
  const computedTrialEver =
    typeof extra.trial_ever === "boolean"
      ? extra.trial_ever
      : Boolean(trial_started_at || trial_expires_at || subscription_until);

  return {
    ...base,
    trial_started_at,
    trial_expires_at,
    subscription_until,
    trial_ever: base.trial_ever ?? (computedTrialEver ? true : null),
    // если какие-то основные поля отсутствуют в base — аккуратно дополним
    status: base.status ?? (extra.status ?? null),
    until: base.until ?? (extra.until ?? null),
    plan: base.plan ?? (extra.plan ?? null),
    is_auto_renew:
      typeof base.is_auto_renew === "boolean"
        ? base.is_auto_renew
        : typeof extra.is_auto_renew === "boolean"
        ? extra.is_auto_renew
        : null,
    needs_policy:
      typeof base.needs_policy === "boolean"
        ? base.needs_policy
        : typeof extra.needs_policy === "boolean"
        ? extra.needs_policy
        : null,
    has_access:
      typeof base.has_access === "boolean"
        ? base.has_access
        : Boolean(extra.has_access),
  };
}

// ===== Основной снимок статуса с бэка ======================================
export async function getAccessStatus(): Promise<AccessSnapshot> {
  const tg = getTelegramUserId();
  if (!tg) return { ok: false, has_access: false };

  // Попытка 1: /api/payments/status (канон для доступа)
  try {
    const u = new URL(apiUrl("/api/payments/status"), window.location.origin);
    u.searchParams.set("tg_user_id", String(tg));
    const r = await fetch(u.toString(), { credentials: "omit" });

    if (r.ok) {
      const raw = (await r.json()) as any;

      // Базовый слепок из payments/status
      let snap: AccessSnapshot = {
        ok: true,
        has_access:
          typeof raw?.has_access === "boolean"
            ? Boolean(raw.has_access)
            : false,
        status: (raw?.status ?? null) as any,
        until: raw?.until ?? null,
        plan: (raw?.plan ?? null) as any,
        is_auto_renew:
          typeof raw?.is_auto_renew === "boolean" ? raw.is_auto_renew : null,
        needs_policy:
          typeof raw?.needs_policy === "boolean" ? raw.needs_policy : null,
        trial_ever:
          typeof raw?.trial_ever === "boolean" ? raw.trial_ever : null,
      };

      // Если payments/status уже дал «историю» — положим
      if (raw?.trial_started_at) snap.trial_started_at = raw.trial_started_at;
      if (raw?.trial_expires_at) snap.trial_expires_at = raw.trial_expires_at;
      if (raw?.subscription_until) snap.subscription_until = raw.subscription_until;

      // ДОП. ШАГ: обогатим историями через /api/access/status
      try {
        const u2 = new URL(apiUrl("/api/access/status"), window.location.origin);
        u2.searchParams.set("tg_user_id", String(tg));
        const r2 = await fetch(u2.toString(), { credentials: "omit" });
        if (r2.ok) {
          const raw2 = await r2.json();
          snap = mergeSnapshots(snap, raw2);
        }
      } catch {
        // молчим, базовый snap уже есть
      }

      // Если has_access явно не пришёл, посчитаем по status+until
      if (typeof snap.has_access !== "boolean") {
        const active = snap.status === "active" && isFuture(snap.until ?? null);
        const trial = snap.status === "trial" && isFuture(snap.until ?? null);
        snap.has_access = active || trial;
      }

      // Если until отсутствует, но есть явные until по полям — добьём
      if (!snap.until) {
        if (isFuture(snap.subscription_until ?? null)) snap.until = snap.subscription_until!;
        else if (isFuture(snap.trial_expires_at ?? null)) snap.until = snap.trial_expires_at!;
        else if (snap.trial_started_at) snap.until = addDaysISO(snap.trial_started_at, 5);
      }

      return snap;
    }
  } catch {
    // пойдём на fallback
  }

  // Попытка 2 (fallback/совместимость): /api/access/status
  try {
    const u = new URL(apiUrl("/api/access/status"), window.location.origin);
    u.searchParams.set("tg_user_id", String(tg));
    const r = await fetch(u.toString(), { credentials: "omit" });
    if (r.ok) {
      const raw = (await r.json()) as any;

      if (
        "has_access" in raw ||
        "status" in raw ||
        "until" in raw ||
        "plan" in raw ||
        "is_auto_renew" in raw ||
        "needs_policy" in raw
      ) {
        const snap: AccessSnapshot = {
          ok: true,
          has_access: Boolean(raw?.has_access),
          status: (raw?.status ?? null) as any,
          until: raw?.until ?? null,
          plan: (raw?.plan ?? null) as any,
          is_auto_renew:
            typeof raw?.is_auto_renew === "boolean" ? raw.is_auto_renew : null,
          needs_policy:
            typeof raw?.needs_policy === "boolean" ? raw.needs_policy : null,
          trial_ever:
            typeof raw?.trial_ever === "boolean" ? raw.trial_ever : null,
          trial_started_at: raw?.trial_started_at ?? null,
          trial_expires_at: raw?.trial_expires_at ?? null,
          subscription_until: raw?.subscription_until ?? null,
        };
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
        is_auto_renew:
          typeof raw?.is_auto_renew === "boolean" ? raw.is_auto_renew : null,
        needs_policy: null,
        trial_ever: null,
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

      // вычислим trial_ever для совсем старого ответа
      snap.trial_ever = Boolean(
        snap.trial_started_at || snap.trial_expires_at || snap.subscription_until
      );

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
