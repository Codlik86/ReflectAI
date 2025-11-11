// src/lib/telegram.ts
// Хелперы для Telegram WebApp + атрибуция через start_param + авто-роутинг

import WebApp from "@twa-dev/sdk";

let _inited = false;

/** Безопасная, идемпотентная инициализация: вне Telegram — не падаем */
export function initTelegram(): void {
  if (_inited) return;
  _inited = true;
  try {
    WebApp.ready?.();
    WebApp.expand?.();
  } catch {
    /* noop */
  }
}

/** Достаём "сырое" API Telegram (если есть) */
export function getTelegram() {
  const tg = (window as any)?.Telegram?.WebApp as typeof WebApp | undefined;
  try {
    if (tg && !tg.isExpanded) tg.expand?.();
  } catch { /* noop */ }
  return tg;
}

/** Тип для initDataUnsafe.user (минимум, что нам нужно) */
export type TgUser = {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
} | null;

/** Пользователь из initDataUnsafe (если запущено как webapp) */
export function getUserFromInitData(): TgUser {
  try {
    const tg = getTelegram();
    const user = tg?.initDataUnsafe?.user;
    if (!user) return null;
    return {
      id: user.id,
      first_name: user.first_name,
      last_name: user.last_name,
      username: user.username,
    };
  } catch {
    return null;
  }
}

/* =========================
   START PARAM: парсинг/маршруты
   ========================= */

/**
 * Источники старта в Telegram mini app:
 * 1) tg.initDataUnsafe.start_param (если открыто внутри Telegram)
 * 2) параметр URL `tgWebAppStartParam` (если открыто прямой ссылкой)
 * 3) fallback: query `start`/`s` в URL (на всякий случай)
 */
export function getStartParam(): string | null {
  try {
    const tg = getTelegram();
    const fromTg = tg?.initDataUnsafe?.start_param;
    if (fromTg) return sanitizeStart(fromTg);

    const url = new URL(window.location.href);
    const fromUrl =
      url.searchParams.get("tgWebAppStartParam") ??
      url.searchParams.get("start") ??
      url.searchParams.get("s");

    return fromUrl ? sanitizeStart(fromUrl) : null;
  } catch {
    return null;
  }
}

function sanitizeStart(v: string): string {
  try {
    return decodeURIComponent(v).trim();
  } catch {
    return v.trim();
  }
}

export const START_ROUTES: Record<string, string> = {
  home: "/",
  exercises: "/exercises",
  meditations: "/meditations",
  paywall: "/paywall",
  "breath-46": "/exercises/breath-46",
  "breath-4444": "/exercises/breath-4444",
  "breath-478": "/exercises/breath-478",
  "breath-333": "/exercises/breath-333",
  pmr: "/exercises/pmr",
  grounding: "/exercises/grounding",
  "body-scan": "/exercises/body-scan",
  "thought-labeling": "/exercises/thought-labeling",
  b46: "/exercises/breath-46",
  b4444: "/exercises/breath-4444",
  b478: "/exercises/breath-478",
  b333: "/exercises/breath-333",
  bs: "/exercises/body-scan",
  tl: "/exercises/thought-labeling",
};

export function parseStartParam(raw: string | null): {
  routeKey: string | null;
  extras: Record<string, string>;
} {
  if (!raw) return { routeKey: null, extras: {} };

  const looksLikeQuery = raw.includes("=") || raw.includes("&");
  if (looksLikeQuery) {
    const query = new URLSearchParams(raw);
    const routeKey =
      query.get("route") || query.get("r") || query.get("page") || query.get("p");
    const extras: Record<string, string> = {};
    query.forEach((val, key) => {
      if (!["route", "r", "page", "p"].includes(key)) extras[key] = val;
    });
    return { routeKey: routeKey?.trim() || null, extras };
  }

  return { routeKey: raw.trim(), extras: {} };
}

export function routeByStartParam(
  navigate: (to: string) => void,
  fallback: string = "/"
): void {
  const raw = getStartParam();
  const { routeKey } = parseStartParam(raw);

  if (routeKey && START_ROUTES[routeKey]) {
    navigate(START_ROUTES[routeKey]);
    return;
  }
  navigate(fallback);
}

/* =========================
   Вспомогательные действия с UI Telegram
   ========================= */

export function showMainButton(text = "Открыть бот") {
  try {
    const tg = getTelegram();
    tg?.MainButton?.setText?.(text);
    tg?.MainButton?.show?.();
  } catch { /* noop */ }
}

export function hideMainButton() {
  try {
    getTelegram()?.MainButton?.hide?.();
  } catch { /* noop */ }
}

export function setBackButton(enabled: boolean, handler?: () => void) {
  try {
    const tg = getTelegram();
    if (!tg) return;

    if (enabled) {
      tg.BackButton?.show?.();
      if (handler) tg.BackButton?.onClick?.(handler);
    } else {
      tg.BackButton?.hide?.();
      if (handler) tg.BackButton?.offClick?.(handler);
    }
  } catch { /* noop */ }
}

export function openExternalLink(url: string) {
  try {
    const tg = getTelegram();
    if (tg?.openLink) tg.openLink(url, { try_instant_view: false });
    else window.open(url, "_blank", "noopener,noreferrer");
  } catch {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

/* =========================
   ДОПОЛНИТЕЛЬНО: raw initData + userId
   ========================= */

/** Сырые initData (строка) для подписи запросов на бэк */
export function getInitDataRaw(): string {
  try {
    const tg = getTelegram() as any;
    const raw = tg?.initData;
    return typeof raw === "string" ? raw : "";
  } catch {
    return "";
  }
}

/** Удобный хелпер, если нужно быстро вытащить id пользователя */
export function getTelegramUserId(): number | null {
  const u = getUserFromInitData();
  return u?.id ?? null;
}
