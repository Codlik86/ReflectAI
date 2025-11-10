// src/components/Header.tsx
import * as React from "react";
import { getTelegram } from "../lib/telegram";

// Для логотипа используем public/ и BASE_URL — так не будет проблем с импортом файлов и пробелами
const base = (import.meta as any)?.env?.BASE_URL || "/";
const pub = (p: string) => `${base}${p}`.replace(/\/+/, "/");

// Имя бота — из ENV, с дефолтом
const BOT_USERNAME =
  (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";

function openBotHelp() {
  const startPayload = "help";
  const tgDeep = `tg://resolve?domain=${encodeURIComponent(BOT_USERNAME)}&start=${encodeURIComponent(startPayload)}`;
  const httpsUrl = `https://t.me/${BOT_USERNAME}?start=${encodeURIComponent(startPayload)}`;

  const tg: any = getTelegram();
  const wa: any = tg?.WebApp || tg;

  const safeClose = () => {
    try {
      wa?.close?.();
    } catch {}
  };

  try {
    if (wa?.openTelegramLink) {
      // Пробуем deep-link (внутри Telegram предпочтительно)
      wa.openTelegramLink(tgDeep);
      // Параллельно даём https-фоллбек (если deep-link не сработал)
      wa.openTelegramLink(httpsUrl);
    } else if (typeof window !== "undefined") {
      // Обычный веб: сначала deep-link, потом https-фоллбек
      try { window.location.href = tgDeep; } catch {}
      window.open(httpsUrl, "_blank", "noopener,noreferrer");
    }
  } catch {
    window.open(httpsUrl, "_blank", "noopener,noreferrer");
  } finally {
    // Мягко закрываем webview после запуска перехода
    setTimeout(safeClose, 120);
  }
}

export default function Header() {
  return (
    <header
      className="mx-5 mt-5 rounded-2xl bg-white px-4 py-3
                 flex items-center justify-between select-none"
    >
      <div className="flex items-center gap-2">
        {/* Логотип из public/ (logo-pomni.svg) */}
        <img
          src={pub("logo-pomni.svg")}
          alt="Помни"
          width={24}
          height={24}
          className="w-6 h-6 object-contain"
          draggable={false}
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
        />
        <div className="text-[16px] font-semibold">ПОМНИ</div>
      </div>

      <button
        type="button"
        onClick={openBotHelp}
        className="text-[16px] font-medium text-ink-900 hover:opacity-80 active:opacity-70"
      >
        Связаться
      </button>
    </header>
  );
}
