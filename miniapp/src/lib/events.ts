import { fetchJson } from "./api";
import { getTelegramUserId } from "./telegram";

async function waitTelegramId(maxTries = 12, delayMs = 120): Promise<number | null> {
  for (let i = 0; i < maxTries; i++) {
    const id = getTelegramUserId();
    if (id) return id;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return getTelegramUserId();
}

export async function trackEvent(
  event: string,
  action?: string | null,
  meta?: Record<string, unknown> | null
): Promise<void> {
  const tg = await waitTelegramId();
  if (!tg) return;
  try {
    await fetchJson("/api/events/track", {
      json: {
        tg_user_id: tg,
        event,
        action: action ?? null,
        meta: meta ?? null,
      },
    });
  } catch {
    // swallow analytics errors
  }
}
