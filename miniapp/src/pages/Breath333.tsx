// src/pages/Breath333.tsx
import { useRef, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";

type Phase = "intro" | "idle" | "inhale" | "hold" | "exhale" | "done";

const INHALE_SEC = 3;
const HOLD_SEC   = 3;
const EXHALE_SEC = 3;
const TOTAL_CYCLES = 8;

// === TAЧ-ХЕЛПЕРЫ: убираем «двойной тап» на мобильных ===
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

export default function Breath333() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>("intro");
  const [cycle, setCycle] = useState(1);
  const [msLeft, setMsLeft] = useState(0);
  const [progress, setProgress] = useState(0);
  const [paused, setPaused] = useState(false);

  const rafId = useRef<number | null>(null);
  const endAt = useRef<number>(0);
  const pausedRef = useRef(false);
  const runningRef = useRef(false);
  const currentDurMs = useRef(0);

  const stopRaf = () => {
    if (rafId.current) cancelAnimationFrame(rafId.current);
    rafId.current = null;
  };

  const resetAll = () => {
    stopRaf();
    setPhase("idle");
    setCycle(1);
    setMsLeft(0);
    setProgress(0);
    setPaused(false);
    pausedRef.current = false;
    runningRef.current = false;
  };

  const begin = () => resetAll();

  const startPhase = (next: Phase) => {
    setPhase(next);
    runningRef.current = true;

    const durSec =
      next === "inhale" ? INHALE_SEC :
      next === "hold"   ? HOLD_SEC   :
      EXHALE_SEC;

    const durMs = durSec * 1000;
    currentDurMs.current = durMs;
    endAt.current = performance.now() + durMs;

    tick(durMs, () => {
      // последовательность: вдох → пауза → выдох → (след. цикл)
      if (next === "inhale")      startPhase("hold");
      else if (next === "hold")   startPhase("exhale");
      else {
        if (cycle < TOTAL_CYCLES) {
          setCycle(c => c + 1);
          startPhase("inhale");
        } else {
          runningRef.current = false;
          setPhase("done");
        }
      }
    });
  };

  const tick = (totalMs: number, onFinish: () => void) => {
    stopRaf();
    const loop = () => {
      if (pausedRef.current || !runningRef.current) {
        rafId.current = requestAnimationFrame(loop);
        return;
      }
      const left = Math.max(0, endAt.current - performance.now());
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

  const start = () => { if (phase === "idle") startPhase("inhale"); };

  const pauseToggle = () => {
    if (phase === "idle" || phase === "intro" || phase === "done") return;
    pausedRef.current = !pausedRef.current;
    setPaused(pausedRef.current);
    if (!pausedRef.current) {
      // пересчитать дедлайн после паузы
      endAt.current = performance.now() + msLeft;
    }
  };

  const stop = () => resetAll();

  // Навигация по циклам
  const goStart = () => resetAll();
  const goPrev  = () => { const prev = Math.max(1, cycle - 1); resetAll(); setCycle(prev); };
  const goNext  = () => { const next = Math.min(TOTAL_CYCLES, cycle + 1); resetAll(); setCycle(next); };
  const goExercises = () => navigate("/exercises");

  // Вспомогательные отображения
  const mm = Math.floor(msLeft / 1000 / 60).toString().padStart(2, "0");
  const ss = Math.floor((msLeft / 1000) % 60).toString().padStart(2, "0");

  const phaseBadge =
    phase === "inhale" ? (
      <span className="inline-flex items-center rounded-full bg-blue-50 text-blue-500 text-[12px] px-2 py-1 font-medium">Вдох</span>
    ) : phase === "hold" ? (
      <span className="inline-flex items-center rounded-full bg-amber-50 text-amber-600 text-[12px] px-2 py-1 font-medium">Пауза</span>
    ) : phase === "exhale" ? (
      <span className="inline-flex items-center rounded-full bg-green-50 text-green-700 text-[12px] px-2 py-1 font-medium">Выдох</span>
    ) : null;

  const circleColor = phase === "idle"
  ? "#22C55E" // зелёный в состоянии до старта
  : phase === "inhale" ? "#3B82F6"
  : phase === "exhale" ? "#22C55E"
  : "#F59E0B";

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Дыхание 3–3–3" to="/exercises" />
      <div className="px-5" style={{ height: "clamp(12px,2.2vh,20px)" }} />

      <div className="px-5 space-y-4" style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}>
        {phase === "intro" && (
          <div className="rounded-3xl bg-white p-5 space-y-4">
            <div className="text-[18px] font-medium text-ink-900">Быстрое выравнивание</div>
            <div className="text-[16px] leading-7 text-ink-700">
              Вдох 3 сек — пауза 3 сек — выдох 3 сек. Всего 8 циклов. Дышим спокойно, без усилия.
            </div>
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

        {(phase === "idle" || phase === "inhale" || phase === "hold" || phase === "exhale") && (
          <div className="rounded-3xl bg-white p-5">
            <div className="flex items-center justify-between">
              <div className="text-[16px] text-ink-600">Цикл {Math.min(cycle, TOTAL_CYCLES)}/{TOTAL_CYCLES}</div>
              <div className="text-[16px] text-ink-600">{phase === "idle" ? "00:00" : `${mm}:${ss}`}</div>
            </div>

            <div className="mt-2 text-center">{phaseBadge}</div>

            <div className="mt-4 flex justify-center">
              <CircleProgress
                percent={phase === "idle" ? 0 : progress}
                color={circleColor}
                label={phase === "idle" ? "0" : Math.ceil(msLeft / 1000).toString()}
              />
            </div>

            <div className="mt-3 text-center text-[14px] text-ink-600">
              {phase === "idle"   && "Готов к старту: вдох 3 сек → пауза 3 сек → выдох 3 сек."}
              {phase === "inhale" && "Вдыхай носом, расширяя живот."}
              {phase === "hold"   && "Пауза — мягкая задержка без напряжения."}
              {phase === "exhale" && "Выдыхай ровно и спокойно."}
            </div>

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
                  className="btn btn-primary"
                  style={touchBtnStyle}
                >
                  {paused ? "Продолжить" : "Пауза"}
                </button>
              )}
              <button
                type="button"
                onClick={stop}
                onPointerDown={onPress(stop)}
                className="btn btn-stop"
                style={touchBtnStyle}
              >
                Остановить
              </button>
            </div>

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
                  disabled={cycle === 1}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                  style={touchBtnStyle}
                >
                  Назад
                </button>
                <button
                  type="button"
                  onClick={goNext}
                  onPointerDown={onPress(goNext)}
                  disabled={cycle === TOTAL_CYCLES}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                  style={touchBtnStyle}
                >
                  Вперёд
                </button>
              </div>
            </div>
          </div>
        )}

        {phase === "done" && (
          <div className="rounded-3xl border border-green-200 bg-green-50 p-6 text-center">
            <div className="text-[18px] font-semibold text-ink-900 mb-1">Готово — ритм выровнялся</div>
            <div className="text-[15px] text-ink-700">Можно повторить ещё раз или вернуться к списку.</div>
            <div className="mt-4 flex items-center justify-center gap-3">
              <button
                type="button"
                onClick={() => { setPhase("idle"); setCycle(1); }}
                onPointerDown={onPress(() => { setPhase("idle"); setCycle(1); })}
                className="h-10 px-4 rounded-xl bg-[#FFA66B] text-white font-medium"
                style={touchBtnStyle}
              >
                Ещё раз
              </button>
              <button
                type="button"
                onClick={goExercises}
                onPointerDown={onPress(goExercises)}
                className="h-10 px-4 rounded-xl bg-white text-ink-900 flex items-center"
                style={touchBtnStyle}
              >
                К упражнениям
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CircleProgress({
  percent, color, label,
}: { percent: number; color: string; label: string }) {
  const R = 28;
  const C = 2 * Math.PI * R;
  const dash = (C * Math.max(0, Math.min(100, percent))) / 100;

  return (
    <svg width="96" height="96">
      <circle cx="48" cy="48" r={R} stroke="#E6E8EB" strokeWidth="6" fill="none" />
      <circle
        cx="48" cy="48" r={R}
        stroke={color} strokeWidth="6" fill="none"
        strokeDasharray={`${dash} ${C - dash}`}
        transform="rotate(-90 48 48)" strokeLinecap="round"
      />
      <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle" fontSize="18" fill="#111827">
        {label}
      </text>
    </svg>
  );
}
