// src/lib/api.ts
import { getInitDataRaw } from "./telegram";

export function baseUrl() {
  const b = import.meta.env.VITE_API_BASE;
  if (!b) throw new Error("VITE_API_BASE не задан");
  return b.replace(/\/+$/, "");
}

export async function fetchJson<T>(
  url: string,
  init?: RequestInit
): Promise<T> {
  const initData = getInitDataRaw(); // пустая строка вне Telegram — ок
  const res = await fetch(url, {
    method: init?.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
      // ВАЖНО: чтобы бэк мог верифицировать пользователя из мини-аппа
      ...(initData ? { "X-Telegram-Init-Data": initData } : {}),
    },
    body: init?.body,
    credentials: init?.credentials ?? "omit",
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text}` : ""}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}
