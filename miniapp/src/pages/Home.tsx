// src/pages/Home.tsx
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { ensureAccess } from "../lib/guard";

export default function Home() {
  const navigate = useNavigate();

  const pub = React.useCallback((p: string) => {
    const base = import.meta.env.BASE_URL || "/";
    return `${base}${p}`.replace(/\/+/, "/");
  }, []);

  const busyRef = React.useRef(false);
  const navigatedRef = React.useRef(false);
  const safeNavigate = React.useCallback(
    (to: string) => {
      if (navigatedRef.current) return;
      navigatedRef.current = true;
      navigate(to);
      setTimeout(() => (navigatedRef.current = false), 300);
    },
    [navigate]
  );

  // небольшой хелпер: почистить кэш гарда, чтобы не залипал "зелёный" статус
  const clearAccessCache = React.useCallback(() => {
    try {
      sessionStorage.removeItem("ACCESS_OK_UNTIL");
      sessionStorage.removeItem("ACCESS_NO_UNTIL");
    } catch {}
  }, []);

  // Унифицированный хелпер: решает, куда вести — к контенту, на paywall по policy, либо на paywall по оплате
  const guardTo = React.useCallback(
    async (path: string) => {
      if (busyRef.current) return;
      busyRef.current = true;
      try {
        // ВАЖНО: без автозапуска триала
        const snap = await ensureAccess({ startTrial: false });
        if (snap.has_access) {
          safeNavigate(path);
        } else {
          clearAccessCache();
          const reason = snap.needs_policy ? "policy" : "billing";
          safeNavigate(`/paywall?from=${encodeURIComponent(path)}&reason=${reason}`);
        }
      } catch {
        clearAccessCache();
        safeNavigate(`/paywall?from=${encodeURIComponent(path)}`);
      } finally {
        setTimeout(() => (busyRef.current = false), 150);
      }
    },
    [safeNavigate, clearAccessCache]
  );

  const onExercises = () => guardTo("/exercises");
  const onMeditations = () => guardTo("/meditations");

  // ===== helper открытия бота и мягкого закрытия WebView =====
  const openBotWithStart = (startPayload: string) => {
    const bot = (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";
    const wa = (window as any)?.Telegram?.WebApp;
    const tgDeep = `tg://resolve?domain=${encodeURIComponent(bot)}&start=${encodeURIComponent(startPayload)}`;
    const httpsUrl = `https://t.me/${bot}?start=${encodeURIComponent(startPayload)}`;

    try {
      if (wa?.openTelegramLink) {
        wa.openTelegramLink(tgDeep);
      } else if (typeof window !== "undefined") {
        window.location.href = tgDeep;
      }
    } catch {}
    try {
      if (wa?.openTelegramLink) wa.openTelegramLink(httpsUrl);
      else window.open(httpsUrl, "_blank", "noopener,noreferrer");
    } catch {
      window.location.href = httpsUrl;
    }
    setTimeout(() => {
      try { wa?.close?.(); } catch {}
    }, 120);
  };

  // «Поговорить»: БЕЗ автозапуска триала. Если доступа нет — идём на пейвол.
  const onTalk = async () => {
    if (busyRef.current) return;
    busyRef.current = true;

    try {
      const snap = await ensureAccess({ startTrial: false }); // <— ключевое изменение
      if (snap.has_access) {
        openBotWithStart("talk");
        return;
      }
      clearAccessCache();
      const reason = snap.needs_policy ? "policy" : "billing";
      safeNavigate(`/paywall?from=${encodeURIComponent("/")}&reason=${reason}`);
    } catch {
      clearAccessCache();
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
