// src/components/Header.tsx
import * as React from "react";
import { getTelegram } from "../lib/telegram";
import { Link } from "react-router-dom"; // +++ добавили

const base = (import.meta as any)?.env?.BASE_URL || "/";
const pub = (p: string) => `${base}${p}`.replace(/\/+/, "/");

const BOT_USERNAME =
  (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";

function openBotTalk() {
  const startPayload = "talk";
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
      wa.openTelegramLink(tgDeep);
      wa.openTelegramLink(httpsUrl);
    } else if (typeof window !== "undefined") {
      try { window.location.href = tgDeep; } catch {}
      window.open(httpsUrl, "_blank", "noopener,noreferrer");
    }
  } catch {
    window.open(httpsUrl, "_blank", "noopener,noreferrer");
  } finally {
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

      {/* Было: кнопка «Поговорить» → стало: ссылка на /contact «Связаться» */}
      <Link
        to="/contact"
        className="text-[16px] font-medium text-ink-900 hover:opacity-80 active:opacity-70"
      >
        Связаться
      </Link>
    </header>
  );
}
