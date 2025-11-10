import * as React from "react";
import { getTelegram } from "../lib/telegram";

// Для логотипа используем public/ и BASE_URL — так не будет проблем с импортом файлов и пробелами
const base = (import.meta as any)?.env?.BASE_URL || "/";
const pub = (p: string) => `${base}${p}`.replace(/\/+/, "/");

// Имя бота — из ENV, с дефолтом
const BOT_USERNAME =
  (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";

function openBot(start?: string) {
  const url = `https://t.me/${BOT_USERNAME}${
    start ? `?start=${encodeURIComponent(start)}` : ""
  }`;

  // Пытаемся открыть ссылку «по-телеграмному» и закрыть webview
  const tg: any = getTelegram();
  const safeClose = () => {
    try {
      tg?.close?.();
      tg?.WebApp?.close?.();
    } catch {}
  };

  try {
    if (tg?.openTelegramLink) {
      tg.openTelegramLink(url);
      // Дадим переходу стартануть и аккуратно закроем webview
      setTimeout(safeClose, 120);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
      // Если не WebApp — закрывать нечего, но на всякий случай попробуем
      setTimeout(safeClose, 0);
    }
  } catch {
    window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(safeClose, 0);
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
        onClick={() => openBot("miniapp")}
        className="text-[16px] font-medium text-ink-900 hover:opacity-80 active:opacity-70"
      >
        Открыть бот
      </button>
    </header>
  );
}
