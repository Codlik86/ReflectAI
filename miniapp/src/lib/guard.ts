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

// ====== кэши/ограничители ======
const POSITIVE_CACHE_KEY = "ACCESS_OK_UNTIL";     // ms timestamp
const NEGATIVE_CACHE_KEY = "ACCESS_NO_UNTIL";     // ms timestamp (короткий)
const COOLDOWN_MS = 400;                          // не дергать чаще, чем раз в 400мс
const POSITIVE_CACHE_MS = 60_000;                 // «зелёное» окно 60с
const NEGATIVE_CACHE_MS = 1_000;                  // анти-луп на 1с

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

function getPositiveCachedOk(): boolean {
  return getTS(POSITIVE_CACHE_KEY) > nowMs();
}
function getNegativeCachedNo(): boolean {
  return getTS(NEGATIVE_CACHE_KEY) > nowMs();
}

/** ждём появления tg_id (WebApp иногда даёт его с задержкой) */
async function waitTelegramId(maxTries = 5, delayMs = 120): Promise<number | null> {
  for (let i = 0; i < maxTries; i++) {
    const id = getTelegramUserId();
    if (id) return id;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return getTelegramUserId();
}

/** Единый гард. Возвращает снимок доступа. Дедупликация + кэши. */
export async function ensureAccess(autoStartTrial: boolean = true): Promise<AccessSnapshot> {
  // 1) короткий «анти-луп», если нас уже недавно трогали
  const since = nowMs() - lastCallAt;
  if (since < COOLDOWN_MS) {
    // отдаём, чем можем: если есть положительный кэш — «проход»,
    // если отрицательный — «нет доступа», иначе продолжаем.
    if (getPositiveCachedOk()) {
      return { ok: true, has_access: true, reason: "subscription", until: null };
    }
    if (getNegativeCachedNo()) {
      return { ok: true, has_access: false, reason: "none", until: null };
    }
  }
  lastCallAt = nowMs();

  // 2) если уже есть выполняющийся запрос — возвращаем его (без параллельных дублей)
  if (inflight) return inflight;

  inflight = (async () => {
    // 3) быстрый «зелёный» кэш (после подтверждённого доступа)
    if (getPositiveCachedOk()) {
      return { ok: true, has_access: true, reason: "subscription", until: null };
    }

    // 4) быстрый «красный» кэш (короткий, защищает от петель)
    if (getNegativeCachedNo()) {
      return { ok: true, has_access: false, reason: "none", until: null };
    }

    const tg = await waitTelegramId();
    if (!API_BASE || !tg) {
      // сетевой/иниц. сбой — не валим пользователя, но ставим короткий «красный» кэш
      putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
      return { ok: false, has_access: false, reason: "none", until: null };
    }

    // ВАЖНО: оставляем твой эндпойнт как есть
    const url = new URL(`${API_BASE}/api/payments/status`);
    url.searchParams.set("tg_user_id", String(tg));
    if (autoStartTrial) url.searchParams.set("start_trial", "1");

    let dto: StatusDTO | null = null;
    try {
      const res = await fetch(url.toString(), { credentials: "omit" });
      if (!res.ok) throw new Error(String(res.status));
      dto = (await res.json()) as StatusDTO;
    } catch {
      // если положительный кэш был — пропускаем, иначе — короткий «красный»
      if (getPositiveCachedOk()) {
        return { ok: true, has_access: true, reason: "subscription", until: null };
      }
      putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
      return { ok: false, has_access: false, reason: "none", until: null };
    }

    const subActive = dto?.status === "active" && isFuture(dto?.until);
    const trialActive = dto?.status === "trial" && isFuture(dto?.until);

    if (subActive || trialActive) {
      putTS(POSITIVE_CACHE_KEY, POSITIVE_CACHE_MS); // 60с окно без повторных проверок
      return {
        ok: true,
        has_access: true,
        reason: subActive ? "subscription" : "trial",
        until: dto?.until ?? null,
      };
    }

    // нет доступа — ставим короткий «красный» кэш, чтобы не было лупов
    putTS(NEGATIVE_CACHE_KEY, NEGATIVE_CACHE_MS);
    return { ok: true, has_access: false, reason: "none", until: dto?.until ?? null };
  })();

  try {
    return await inflight;
  } finally {
    inflight = null; // снимаем «занято»
  }
}
