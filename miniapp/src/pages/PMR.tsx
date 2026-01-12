// src/pages/PMR.tsx
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import { trackEvent } from "../lib/events";
import BackBar from "../components/BackBar";
import pmr from "../data/pmr.ru";

type Phase = "intro" | "idle" | "tense" | "relax" | "done";

// === TOUCH-ХЕЛПЕРЫ: фикс «дабл-тапа» на мобильных ===
const touchBtnStyle: CSSProperties = {
  WebkitTapHighlightColor: "transparent",
  touchAction: "manipulation",
};
const onPress =
  (fn: () => void) =>
  (e: React.PointerEvent | React.MouseEvent) => {
    if ("pointerType" in e && (e as React.PointerEvent).pointerType === "touch") {
      e.preventDefault();
      e.stopPropagation();
      fn();
      return;
    }
    if (!(e as any).pointerType) fn();
  };

export default function PMR() {
  const navigate = useNavigate();
  const groups = pmr.groups;

  const [idx, setIdx] = useState(0);
  const idxRef = useRef(0);

  const [phase, setPhase] = useState<Phase>("intro");
  const [tab, setTab] = useState<"tension" | "relaxation">("tension");

  const [msLeft, setMsLeft] = useState(0);
  const [progress, setProgress] = useState(0);

  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);

  // таймер
  const rafId = useRef<number | null>(null);
  const endAt = useRef<number>(0);
  const pauseStartedAt = useRef<number | null>(null);

  const stopRaf = () => {
    if (rafId.current) cancelAnimationFrame(rafId.current);
    rafId.current = null;
  };

  const getDurationsMs = () => {
    const g = groups[idxRef.current];
    const tenseMs = (g?.tenseSeconds ?? pmr.defaultTenseSec) * 1000;
    const relaxMs = (g?.relaxSeconds ?? pmr.defaultRelaxSec) * 1000;
    return { tenseMs, relaxMs };
  };

  const tick = (totalMs: number, onFinish: () => void) => {
    stopRaf();
    const loop = () => {
      if (pausedRef.current) {
        rafId.current = requestAnimationFrame(loop);
        return;
      }
      const now = performance.now();
      const left = Math.max(0, endAt.current - now);
      setMsLeft(left);
      setProgress(Math.min(100, ((totalMs - left) / totalMs) * 100));
      if (left <= 0) {
        stopRaf();
        onFinish();
        return;
      }
      rafId.current = requestAnimationFrame(loop);
    };
    rafId.current = requestAnimationFrame(loop);
  };

  const startPhase = (next: Phase) => {
    setPhase(next);
    if (next === "tense") {
      setTab("tension");
      const { tenseMs } = getDurationsMs();
      endAt.current = performance.now() + tenseMs;
      tick(tenseMs, () => startPhase("relax"));
    } else if (next === "relax") {
      setTab("relaxation");
      const { relaxMs } = getDurationsMs();
      endAt.current = performance.now() + relaxMs;
      tick(relaxMs, () => {
        // авто-переход к следующей группе и авто-старт «Напрячь»
        if (idxRef.current < groups.length - 1) {
          setProgress(0);
          setMsLeft(0);
          setTab("tension");
          setPaused(false);
          pausedRef.current = false;

          setIdx((v) => {
            const nv = v + 1;
            idxRef.current = nv;
            return nv;
          });
          requestAnimationFrame(() => startPhase("tense"));
        } else {
          setPhase("done");
        }
      });
    }
  };

  // управление
  const begin = () => {
    stopRaf();
    idxRef.current = 0;
    setIdx(0);
    setProgress(0);
    setMsLeft(0);
    setPaused(false);
    pausedRef.current = false;
    setTab("tension");
    setPhase("idle"); // без автозапуска
  };

  const start = () => {
    if (phase === "idle") startPhase("tense");
  };

  const pauseToggle = () => {
    if (phase !== "tense" && phase !== "relax") return;
    if (!pausedRef.current) {
      setPaused(true);
      pausedRef.current = true;
      pauseStartedAt.current = performance.now();
    } else {
      setPaused(false);
      pausedRef.current = false;
      if (pauseStartedAt.current != null) {
        const delta = performance.now() - pauseStartedAt.current;
        endAt.current += delta; // продлеваем дедлайн на длительность паузы
        pauseStartedAt.current = null;
      }
    }
  };

  const stopAll = () => {
    stopRaf();
    setPhase("idle");
    setProgress(0);
    setMsLeft(0);
    setPaused(false);
    pausedRef.current = false;
    setTab("tension");
  };

  // нижняя навигация
  const goStart = () => {
    trackEvent("miniapp_action", "exercise_started", { exercise_id: "pmr" });
    stopRaf();
    idxRef.current = 0;
    setIdx(0);
    setPhase("idle");
    setProgress(0);
    setMsLeft(0);
    setPaused(false);
    pausedRef.current = false;
    setTab("tension");
  };

  const goPrev = () => {
    if (idxRef.current === 0) return;
    stopRaf();
    const nv = idxRef.current - 1;
    idxRef.current = nv;
    setIdx(nv);
    setPhase("idle");
    setProgress(0);
    setMsLeft(0);
    setPaused(false);
    pausedRef.current = false;
    setTab("tension");
  };

  const goNext = () => {
    if (idxRef.current >= groups.length - 1) return;
    stopRaf();
    const nv = idxRef.current + 1;
    idxRef.current = nv;
    setIdx(nv);
    setPhase("idle");
    setProgress(0);
    setMsLeft(0);
    setPaused(false);
    pausedRef.current = false;
    setTab("tension");
  };

  useEffect(() => {
    return () => stopRaf();
  }, []);

  const g = groups[idx];
  const mm = Math.floor(msLeft / 1000 / 60).toString().padStart(2, "0");
  const ss = Math.floor((msLeft / 1000) % 60).toString().padStart(2, "0");

  const phaseLabel =
    phase === "tense" ? (
      <span className="inline-flex items-center rounded-full bg-red-50 text-red-700 text-[12px] px-2 py-1 font-medium">
        Напрячь
      </span>
    ) : phase === "relax" ? (
      <span className="inline-flex items-center rounded-full bg-green-50 text-green-700 text-[12px] px-2 py-1 font-medium">
        Расслабить
      </span>
    ) : null;

  return (
    <div className="min-h-dvh flex flex-col">
      {/* вместо to — явный onBack: остановить всё и уйти на /exercises */}
      <BackBar
        title="Мышечная релаксация"
        onBack={() => {
          stopRaf();
          navigate("/exercises");
        }}
      />
      <div className="px-5" style={{ height: "clamp(12px,2.2vh,20px)" }} />

      <div className="px-5 space-y-4" style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}>
        {/* Интро */}
        {phase === "intro" && (
          <div className="rounded-3xl bg-white p-5 space-y-4">
            <div className="text-[18px] font-medium text-ink-900">Расслабление по группам мышц</div>
            <div className="text-[16px] leading-7 text-ink-700">
              По очереди напрягай мышцу ~5–10 сек и мягко отпускай. Следи за разницей ощущений.
            </div>
            <ul className="rounded-2xl bg-white border border-black/5 p-4 space-y-2">
              <li>Кисти и предплечья → плечи и лопатки</li>
              <li>Лицо и челюсть → шея</li>
              <li>Грудь/спина → живот/поясница → таз</li>
              <li>Бёдра → голени → стопы</li>
            </ul>
            <div className="flex items-center justify-center">
              <button
                type="button"
                onClick={begin}
                onPointerDown={onPress(begin)}
                className="btn btn-primary"
                style={touchBtnStyle}
              >
                Начать
              </button>
            </div>
          </div>
        )}

        {/* Основной экран */}
        {(phase === "idle" || phase === "tense" || phase === "relax") && (
          <div className="rounded-3xl bg-white p-5">
            <div className="flex items-center justify-between">
              <div className="text-[16px] text-ink-600">
                Группа {idx + 1} из {groups.length}
              </div>
              <div className="text-[16px] text-ink-600">{phase === "idle" ? "00:00" : `${mm}:${ss}`}</div>
            </div>

            {/* название группы — всегда крупно */}
            <div className="mt-3 text-center">
              <div className="text-[20px] font-semibold text-ink-900">{g.name}</div>
              {/* бейдж фазы под заголовком */}
              {phaseLabel && <div className="mt-2">{phaseLabel}</div>}
            </div>

            {/* индикатор */}
            <div className="mt-4 flex justify-center">
              <CircleProgress
                percent={phase === "idle" ? 0 : progress}
                color={phase === "tense" ? "#EF4444" : "#22C55E"}
                label={phase === "idle" ? "0" : Math.ceil(msLeft / 1000).toString()}
              />
            </div>

            {/* табы шагов */}
            <div className="mt-4 rounded-2xl border border-black/5 overflow-hidden">
              <div className="flex">
                <button
                  type="button"
                  onClick={() => setTab("tension")}
                  onPointerDown={onPress(() => setTab("tension"))}
                  className={`flex-1 px-3 py-2 text-sm font-medium ${
                    tab === "tension" || phase === "tense"
                      ? "bg-red-50 text-ink-900 border-b-2 border-red-500"
                      : "bg-gray-50 text-ink-600"
                  }`}
                  style={touchBtnStyle}
                >
                  Шаги напряжения
                </button>
                <button
                  type="button"
                  onClick={() => setTab("relaxation")}
                  onPointerDown={onPress(() => setTab("relaxation"))}
                  className={`flex-1 px-3 py-2 text-sm font-medium ${
                    tab === "relaxation" || phase === "relax"
                      ? "bg-green-50 text-ink-900 border-b-2 border-green-500"
                      : "bg-gray-50 text-ink-600"
                  }`}
                  style={touchBtnStyle}
                >
                  Шаги расслабления
                </button>
              </div>

              <div className="p-3">
                {phase !== "relax" && tab === "tension" ? (
                  <CueList color="red" steps={g.tenseCue} dim={phase === "idle"} />
                ) : (
                  <CueList color="green" steps={g.relaxCue} dim={phase === "idle"} />
                )}
              </div>
            </div>

            {/* управление фазой */}
            <div className="mt-5 flex items-center justify-center gap-3">
              {phase === "idle" ? (
                <button
                  type="button"
                  onClick={start}
                  onPointerDown={onPress(start)}
                  className="btn btn-primary"
                  style={touchBtnStyle}
                >
                  Старт
                </button>
              ) : (
                <button
                  type="button"
                  onClick={pauseToggle}
                  onPointerDown={onPress(pauseToggle)}
                  className={`btn ${paused ? "btn-neutral" : "btn-primary"}`}
                  style={touchBtnStyle}
                >
                  {paused ? "Продолжить" : "Пауза"}
                </button>
              )}
              {/* ФИКС: была ссылка на несуществующий stop → используем stopAll */}
              <button
                type="button"
                onClick={stopAll}
                onPointerDown={onPress(stopAll)}
                className="btn btn-stop"
                style={touchBtnStyle}
              >
                Остановить
              </button>
            </div>

            {/* нижняя навигация */}
            <div className="mt-5 pt-4 border-t border-black/5">
              <div className="flex items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={goStart}
                  onPointerDown={onPress(goStart)}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80"
                  style={touchBtnStyle}
                >
                  Сначала
                </button>
                <button
                  type="button"
                  onClick={goPrev}
                  onPointerDown={onPress(goPrev)}
                  disabled={idx === 0}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                  style={touchBtnStyle}
                >
                  Назад
                </button>
                <button
                  type="button"
                  onClick={goNext}
                  onPointerDown={onPress(goNext)}
                  disabled={idx === groups.length - 1}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                  style={touchBtnStyle}
                >
                  Вперёд
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Финал */}
        {phase === "done" && (
          <div className="rounded-3xl border border-green-200 bg-green-50 p-6 text-center">
            <div className="text-[18px] font-semibold text-ink-900 mb-1">Готово! Напряжение сброшено</div>
            <div className="text-[15px] text-ink-700">
              Можно повторить на отдельных группах или вернуться к списку упражнений.
            </div>
            <div className="mt-4 flex items-center justify-center gap-3">
              <button
                type="button"
                onClick={begin}
                onPointerDown={onPress(begin)}
                className="btn btn-primary"
                style={touchBtnStyle}
              >
                Ещё раз
              </button>
              <button
                type="button"
                onClick={() => navigate("/exercises")}
                onPointerDown={onPress(() => navigate("/exercises"))}
                className="h-10 px-4 rounded-xl bg-white text-ink-900"
                style={touchBtnStyle}
              >
                К списку упражнений
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* вспомогательные */

function CircleProgress({
  percent,
  color,
  label,
}: {
  percent: number;
  color: string;
  label: string;
}) {
  const R = 28;
  const C = 2 * Math.PI * R;
  const dash = (C * percent) / 100;

  return (
    <svg width="96" height="96">
      <circle cx="48" cy="48" r={R} stroke="#E6E8EB" strokeWidth="6" fill="none" />
      <circle
        cx="48" cy="48" r={R}
        stroke={color}
        strokeWidth="6"
        fill="none"
        strokeDasharray={`${dash} ${C - dash}`}
        transform="rotate(-90 48 48)"
        strokeLinecap="round"
      />
      <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle" fontSize="18" fill="#111827">
        {label}
      </text>
    </svg>
  );
}

function CueList({
  steps,
  color,
  dim,
}: {
  steps: string[];
  color: "red" | "green";
  dim?: boolean;
}) {
  const tone = color === "red" ? "bg-red-200 text-red-800" : "bg-green-200 text-green-800";
  return (
    <ol className={`space-y-2 text-sm ${dim ? "opacity-70" : ""}`}>
      {steps.map((s, i) => (
        <li key={i} className="flex items-start gap-2">
          <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded ${tone} text-[12px] font-bold`}>
            {i + 1}
          </span>
          <span className="text-[14px] leading-5 text-ink-900">{s}</span>
        </li>
      ))}
    </ol>
  );
}
