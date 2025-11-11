// src/lib/api.ts
import { getInitDataRaw } from "./telegram";

/** Нормализуем базу API: ENV → localStorage; без хвостового слэша */
function normalizeBase(s?: string | null) {
  if (!s) return "";
  return s.replace(/\/+$/, "");
}

const API_BASE =
  normalizeBase((import.meta as any)?.env?.VITE_API_BASE) ||
  normalizeBase(window.localStorage.getItem("VITE_API_BASE")) ||
  "";

/** Вернёт базовый URL API (может быть пустым — тогда используем same-origin пути) */
export function baseUrl(): string {
  return API_BASE;
}

/** Собрать абсолютный URL к API: если BASE есть — склеим, иначе вернём path как есть */
export function apiUrl(path: string): string {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path; // уже абсолютный
  if (API_BASE) return `${API_BASE}${path}`;
  return path; // same-origin
}

type JsonInit = RequestInit & { json?: unknown };

/** Утилита: аккуратно собрать заголовки без лишнего Content-Type */
function buildHeaders(init?: JsonInit): HeadersInit {
  const headers: Record<string, string> = {};
  const initData = getInitDataRaw();
  if (initData) headers["X-Telegram-Init-Data"] = initData;

  if (init?.json !== undefined || init?.body) {
    const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
    if (!isFormData) headers["Content-Type"] = "application/json";
  }

  return { ...headers, ...(init?.headers || {}) };
}

/**
 * fetchJson: умный фетчер для API.
 * - path может быть абсолютным URL или API-путём ("/api/...").
 * - init.json сериализуется в body как JSON.
 * - Подмешивает X-Telegram-Init-Data.
 * - По умолчанию credentials: "omit".
 */
export async function fetchJson<T>(path: string, init?: JsonInit): Promise<T> {
  const url = apiUrl(path);

  const method = (init?.method || (init?.json ? "POST" : "GET")).toUpperCase();
  const body =
    init?.json !== undefined
      ? JSON.stringify(init.json)
      : init?.body !== undefined
      ? (init.body as BodyInit)
      : undefined;

  const res = await fetch(url, {
    method,
    headers: buildHeaders({ ...init, body }),
    body,
    credentials: init?.credentials ?? "omit",
    redirect: init?.redirect ?? "follow",
    signal: init?.signal,
    cache: init?.cache,
    mode: init?.mode,
    keepalive: init?.keepalive,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text}` : ""}`);
  }

  if (res.status === 204) return undefined as unknown as T;

  const raw = await res.text();
  if (!raw) return undefined as unknown as T;

  return JSON.parse(raw) as T;
}
