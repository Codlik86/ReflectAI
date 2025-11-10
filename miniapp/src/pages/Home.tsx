// src/pages/Home.tsx
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { ensureAccess } from "../lib/guard";

export default function Home() {
  const navigate = useNavigate();

  // helper для путей из public/
  const pub = React.useCallback((p: string) => {
    const base = import.meta.env.BASE_URL || "/";
    return `${base}${p}`.replace(/\/+/, "/");
  }, []);

  // защита от повторных кликов/дубль-навигации
  const busyRef = React.useRef(false);
  const navigatedRef = React.useRef(false);
  const safeNavigate = React.useCallback(
    (to: string) => {
      if (navigatedRef.current) return;
      navigatedRef.current = true;
      navigate(to);
      // снимем флаг чуть позже, чтобы не словить мгновенный дубль
      setTimeout(() => (navigatedRef.current = false), 300);
    },
    [navigate]
  );

  // универсальный гард: проверяем доступ → либо идём в path, либо на paywall
  // ВАЖНО: со страницы Home НЕ запускаем триал автоматически.
  const guardTo = React.useCallback(
    async (path: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      try {
        const snap = await ensureAccess({ startTrial: false });
        if (snap.has_access) {
          safeNavigate(path);
        } else {
          safeNavigate(`/paywall?from=${encodeURIComponent(path)}`);
        }
      } catch {
        safeNavigate(`/paywall?from=${encodeURIComponent(path)}`);
      } finally {
        // лёгкая задержка, чтобы не провоцировать повтор
        setTimeout(() => (busyRef.current = false), 150);
      }
    },
    [safeNavigate]
  );

  const onExercises = () => guardTo("/exercises");
  const onMeditations = () => guardTo("/meditations");

  // «Поговорить»: считаем осмысленным действием → разрешаем автозапуск триала
  const onTalk = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const snap = await ensureAccess({ startTrial: true });
      if (!snap.has_access) {
        safeNavigate(`/paywall?from=${encodeURIComponent("/")}`);
        return;
      }
      const bot = (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";
      const url = `https://t.me/${bot}?start=miniapp`;
      const wa = (window as any)?.Telegram?.WebApp;
      try {
        if (wa?.openTelegramLink) wa.openTelegramLink(url);
        else window.open(url, "_blank", "noopener,noreferrer");
      } catch {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    } catch {
      safeNavigate(`/paywall?from=${encodeURIComponent("/")}`);
    } finally {
      setTimeout(() => (busyRef.current = false), 150);
    }
  };

  return (
    <div className="min-h-dvh flex flex-col">
      {/* Герой со «шаром» */}
      <div className="relative mb-3" style={{ height: "clamp(220px, 48vh, 420px)" }}>
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="blob" />
        </div>
      </div>

      {/* Карточки у низа */}
      <div className="px-5 space-y-3" style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}>
        {/* Упражнения */}
        <button type="button" onClick={onExercises} className="block w-full text-left">
          <div className="card-btn">
            <div className="card-title">Упражнения</div>
            <img src={pub("exercises.png")} alt="Упражнения" className="ml-auto h-24 w-24 object-contain" />
          </div>
        </button>

        {/* Медитации */}
        <button type="button" onClick={onMeditations} className="block w-full text-left">
          <div className="card-btn">
            <div className="card-title">Медитации</div>
            <img src={pub("meditations.png")} alt="Медитации" className="ml-auto h-24 w-24 object-contain" />
          </div>
        </button>

        {/* Поговорить — компактная карточка */}
        <button type="button" onClick={onTalk} className="block w-full text-left">
          <div className="card-btn" style={{ minHeight: 64, padding: "12px 16px", display: "flex", alignItems: "center" }}>
            <div className="card-title">Поговорить</div>
            <img src={pub("talk.png")} alt="Поговорить" className="ml-auto h-24 w-24 object-contain" />
          </div>
        </button>
      </div>
    </div>
  );
}
