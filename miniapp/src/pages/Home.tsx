import * as React from "react";
import { useNavigate } from "react-router-dom";
import { ensureAccess } from "../lib/guard";

// импорт ассетов (Vite перепакует и подставит правильные URL)
import exercisesPng from "../assets/exercises.png";
import meditationsPng from "../assets/meditations.png";
import talkPng from "../assets/talk.png";

export default function Home() {
  const navigate = useNavigate();

  // универсальный гард: проверяем доступ → либо идём в path, либо на paywall
  const guardTo = React.useCallback(
    async (path: string) => {
      try {
        const st = await ensureAccess(true); // автостарт триала, если ещё не запущен
        if (st.has_access) {
          navigate(path);
        } else {
          const ret = encodeURIComponent(path);
          navigate(`/paywall?from=${ret}`);
        }
      } catch {
        const ret = encodeURIComponent(path);
        navigate(`/paywall?from=${ret}`);
      }
    },
    [navigate]
  );

  const onExercises = () => guardTo("/exercises");
  const onMeditations = () => guardTo("/meditations");

  const onTalk = async () => {
    try {
      const st = await ensureAccess(true);
      if (!st.has_access) {
        const ret = encodeURIComponent("/");
        navigate(`/paywall?from=${ret}`);
        return;
      }
      // доступ есть — открываем бота
      window.open(
        "https://t.me/reflectttaibot?start=miniapp",
        "_blank",
        "noopener,noreferrer"
      );
    } catch {
      const ret = encodeURIComponent("/");
      navigate(`/paywall?from=${ret}`);
    }
  };

  return (
    <div className="min-h-dvh flex flex-col">
      {/* Герой со «шаром» */}
      <div
        className="relative mb-3"
        style={{ height: "clamp(220px, 48vh, 420px)" }}
      >
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="blob" />
        </div>
      </div>

      {/* Карточки у низа */}
      <div
        className="px-5 space-y-3"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}
      >
        {/* Упражнения */}
        <button type="button" onClick={onExercises} className="block w-full text-left">
          <div className="card-btn">
            <div className="card-title">Упражнения</div>
            <img
              src={exercisesPng}
              alt="Упражнения"
              className="ml-auto h-24 w-24 object-contain"
            />
          </div>
        </button>

        {/* Медитации */}
        <button type="button" onClick={onMeditations} className="block w-full text-left">
          <div className="card-btn">
            <div className="card-title">Медитации</div>
            <img
              src={meditationsPng}
              alt="Медитации"
              className="ml-auto h-24 w-24 object-contain"
            />
          </div>
        </button>

        {/* Поговорить — компактная карточка (половина высоты) */}
        <button type="button" onClick={onTalk} className="block w-full text-left">
          <div
            className="card-btn"
            style={{
              minHeight: 64, // ниже обычной
              padding: "12px 16px",
              display: "flex",
              alignItems: "center",
            }}
          >
            <div className="card-title">Поговорить</div>
            <img
              src={talkPng}
              alt="Поговорить"
              className="ml-auto h-24 w-24 object-contain"
            />
          </div>
        </button>
      </div>
    </div>
  );
}
