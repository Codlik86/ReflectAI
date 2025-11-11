import { getTelegramUserId } from "./telegram";

type StatusDTO = {
  ok?: boolean;
  status?: "active" | "trial" | "none" | null;
  until?: string | null;
  plan?: "week" | "month" | "quarter" | "year" | null;
  has_access?: boolean;
  needs_policy?: boolean; // НОВОЕ
};

export type AccessSnapshot = {
  ok: boolean;
  has_access: boolean;
  reason: "subscription" | "trial" | "none";
  until: string | null;
  needs_policy?: boolean; // НОВОЕ
};

// ====== API base detection ======
function normalizeBase(s?: string | null) {
  if (!s) return "";
  return s.replace(/\/+$/, "");
}
const API_BASE =
  normalizeBase((import.meta as any)?.env?.VITE_API_BASE) ||
  normalizeBase(window.localStorage.getItem("VITE_API_BASE")) ||
  ""; // пусто = same-origin

const apiUrl = (path: string) => (API_BASE ? `${API_BASE}${path}` : path);

// ====== кэши/ограничители ======
const POSITIVE_CACHE_KEY = "ACCESS_OK_UNTIL"; // ms timestamp
const NEGATIVE_CACHE_KEY = "ACCESS_NO_UNTIL"; // ms timestamp
const COOLDOWN_MS = 400;
const POSITIVE_CACHE_MS = 60_000; // 60s «зелёный»
const NEGATIVE_CACHE_MS = 5_000; // 5s «красный» (анти-луп)

let inflight: Promise<AccessSnapshot> | null = null;
let lastCallAt = 0;

const nowMs = () => Date.now();
const isFuture = (iso?: string | null) => {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) && t > nowMs();
};

const getTS = (key: string) => Number(sessionStorage.getItem(key) || "0");
const putTS = (key: string, msAhead: number) =>
  sessionStorage.setItem(key, String(nowMs() + msAhead));

function positiveCached(): boolean {
  return getTS(POSITIVE_CACHE_KEY) > nowMs();
}
function negativeCached(): boolean {
  return getTS(NEGATIVE_CACHE_KEY) > nowMs();
}

/** ждём появления tg_id (WebApp иногда даёт его с задержкой) */
async function waitTelegramId(maxTries = 12, delayMs = 120): Promise<number | null> {
  for (let i = 0; i < maxTries; i++) {
    const id = getTelegramUserId();
    if (id) return id;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return getTelegramUserId();
}

/**
 * Единый гард. Возвращает снимок доступа. Без редиректов.
 * Совместим с двумя сигнатурами:
 *  - ensureAccess(false)                // не запускать триал
 *  - ensureAccess({ startTrial:false }) // то же
 */
export async function ensureAccess(
  opts: boolean | { startTrial?: boolean } = true
): Promise<AccessSnapshot> {
  const autoStartTrial = typeof opts === "boolean" ? opts : opts.startTrial ?? true;

  // 1) короткий «анти-луп»
  const since = nowMs() - lastCallAt;
  if (since < COOLDOWN_MS) {
    if (positiveCached()) {
      return { ok: true, has_access: true, reason: "subscription", until: null, needs_policy: false };
    }
    if (negativeCached()) {
      return { ok: true, has_access: false, reason: "none", until: null };
    }
  }
  lastCallAt = nowMs();

  // 2) single-flight
  if (inflight) return inflight;

  inflight = (async (): Promise<AccessSnapshot> => {
    // быстрые кэши
    if (positiveCached()) {
      return { ok: true, has_access: true, reason: "subscription", until: null, needs_policy: false };
    }
    if (negativeCached()) {
      return { ok: true, has_access: false, reason: "none", until: null };
    }

    const tg = await waitTelegramId();
    if (!tg) {
      putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
      return { ok: false, has_access: false, reason: "none", until: null };
    }

    // 3) основной статус: GET /api/payments/status?tg_user_id=...(&start_trial=1)
    try {
      const u = new URL(apiUrl("/api/payments/status"), window.location.origin);
      u.searchParams.set("tg_user_id", String(tg));
      if (autoStartTrial) u.searchParams.set("start_trial", "1");

      const res = await fetch(u.toString(), { credentials: "omit" });
      if (res.ok) {
        const dto = (await res.json()) as StatusDTO;

        const until = dto?.until ?? null;
        const subActive = dto?.status === "active" && isFuture(until);
        const trialActive = dto?.status === "trial" && isFuture(until);
        const has =
          typeof dto.has_access === "boolean"
            ? dto.has_access
            : subActive || trialActive;

        // если требуется политика — не зелёный кэш
        if (dto.needs_policy) {
          putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
          return { ok: true, has_access: false, reason: "none", until: null, needs_policy: true };
        }

        if (has) {
          putTS(POSITIVE_CACHE_KEY, POSITIVE_CACHE_MS);
          return {
            ok: true,
            has_access: true,
            reason: trialActive ? "trial" : "subscription",
            until,
            needs_policy: false,
          };
        }
        // нет доступа → пробуем fallback
      }
    } catch {
      // молча уходим на fallback
    }

    // 4) fallback: POST /api/access/check (без start_trial — его нет по контракту)
    try {
      const res = await fetch(apiUrl("/api/access/check"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "omit",
        body: JSON.stringify({ tg_user_id: tg }),
      });
      if (res.ok) {
        const dto = (await res.json()) as { ok?: boolean; until?: string | null };
        const until = (dto?.until ?? null) as string | null;
        const has = Boolean(dto?.ok) && isFuture(until);

        if (has) {
          putTS(POSITIVE_CACHE_KEY, POSITIVE_CACHE_MS);
          return {
            ok: true,
            has_access: true,
            reason: "subscription",
            until,
            needs_policy: false,
          };
        }
        putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
        return { ok: true, has_access: false, reason: "none", until };
      }
    } catch {
      putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
      return { ok: false, has_access: false, reason: "none", until: null };
    }

    // дефолт
    putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
    return { ok: true, has_access: false, reason: "none", until: null };
  })();

  try {
    return await inflight;
  } finally {
    inflight = null;
  }
}
