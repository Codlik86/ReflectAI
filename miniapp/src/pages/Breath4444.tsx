import { useRef, useState } from "react";
import BackBar from "../components/BackBar";

type Phase = "intro" | "idle" | "inhale" | "hold" | "exhale" | "hold2" | "done";

const INHALE_SEC = 4;
const HOLD1_SEC = 4;
const EXHALE_SEC = 4;
const HOLD2_SEC = 4;
const TOTAL_CYCLES = 8;

export default function Breath4444() {
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

  const stopRaf = () => { if (rafId.current) cancelAnimationFrame(rafId.current); rafId.current = null; };

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
      next === "hold"   ? HOLD1_SEC  :
      next === "exhale" ? EXHALE_SEC : HOLD2_SEC;

    const durMs = durSec * 1000;
    currentDurMs.current = durMs;
    endAt.current = performance.now() + durMs;

    tick(durMs, () => {
      // последовательность: inhale → hold → exhale → hold2 → (след. цикл)
      if (next === "inhale")       startPhase("hold");
      else if (next === "hold")    startPhase("exhale");
      else if (next === "exhale")  startPhase("hold2");
      else {
        // закончилась hold2
        if (cycle < TOTAL_CYCLES) {
          setCycle((c) => c + 1);
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
    if (!pausedRef.current) endAt.current = performance.now() + msLeft;
  };
  const stop = () => resetAll();

  const goStart = () => resetAll();
  const goPrev = () => { const prev = Math.max(1, cycle - 1); resetAll(); setCycle(prev); };
  const goNext = () => { const next = Math.min(TOTAL_CYCLES, cycle + 1); resetAll(); setCycle(next); };

  const mm = Math.floor(msLeft / 1000 / 60).toString().padStart(2, "0");
  const ss = Math.floor((msLeft / 1000) % 60).toString().padStart(2, "0");

  const phaseBadge =
    phase === "inhale" ? (
      <span className="inline-flex items-center rounded-full bg-blue-50 text-blue-500 text-[12px] px-2 py-1 font-medium">Вдох</span>
    ) : phase === "hold" || phase === "hold2" ? (
      <span className="inline-flex items-center rounded-full bg-amber-50 text-amber-600 text-[12px] px-2 py-1 font-medium">Пауза</span>
    ) : phase === "exhale" ? (
      <span className="inline-flex items-center rounded-full bg-green-50 text-green-700 text-[12px] px-2 py-1 font-medium">Выдох</span>
    ) : null;

  const circleColor =
    phase === "inhale" ? "#3B82F6" : phase === "exhale" ? "#22C55E" : "#F59E0B";

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Квадратное дыхание 4–4–4–4" to="/exercises" />
      <div className="px-5" style={{ height: "clamp(12px,2.2vh,20px)" }} />

      <div className="px-5 space-y-4" style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}>
        {phase === "intro" && (
          <div className="rounded-3xl bg-white p-5 space-y-4">
            <div className="text-[18px] font-medium text-ink-900">Выравниваемся и снижаем напряжение</div>
            <div className="text-[16px] leading-7 text-ink-700">
              Дышим «квадратом»: вдох — пауза — выдох — пауза. Всего 8 циклов.
            </div>
            <div className="flex items-center justify-center">
              <button onClick={begin} className="btn btn-primary">Начать</button>
            </div>
          </div>
        )}

        {(phase === "idle" || phase === "inhale" || phase === "hold" || phase === "exhale" || phase === "hold2") && (
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
              {phase === "idle"   && "Готов к старту: вдох 4 сек → пауза 4 сек → выдох 4 сек → пауза 4 сек."}
              {phase === "inhale" && "Вдыхай носом, расширяя живот."}
              {phase === "hold"   && "Короткая пауза — мягко задержи дыхание."}
              {phase === "exhale" && "Выдыхай ровно и спокойно."}
              {phase === "hold2"  && "Ещё пауза — мягкая задержка."}
            </div>

            <div className="mt-5 flex items-center justify-center gap-3">
              {phase === "idle" ? (
                <button onClick={start} className="btn btn-primary">Старт</button>
              ) : (
                <button onClick={pauseToggle} className="btn btn-primary">
                  {paused ? "Продолжить" : "Пауза"}
                </button>
              )}
              <button onClick={stop} className="btn btn-stop">Остановить</button>
            </div>

            <div className="mt-5 pt-4 border-t border-black/5">
              <div className="flex items-center justify-center gap-2">
                <button onClick={goStart} className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80">Сначала</button>
                <button onClick={goPrev} disabled={cycle === 1} className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30">Назад</button>
                <button onClick={goNext} disabled={cycle === TOTAL_CYCLES} className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30">Вперёд</button>
              </div>
            </div>
          </div>
        )}

        {phase === "done" && (
          <div className="rounded-3xl border border-green-200 bg-green-50 p-6 text-center">
            <div className="text-[18px] font-semibold text-ink-900 mb-1">Готово — цикл «квадрата» завершён</div>
            <div className="text-[15px] text-ink-700">Можно повторить ещё раз или вернуться к списку.</div>
            <div className="mt-4 flex items-center justify-center gap-3">
              <button onClick={() => { setPhase("idle"); setCycle(1); }} className="h-10 px-4 rounded-xl bg-[#FFA66B] text-white font-medium">Ещё раз</button>
              <a href="/exercises" className="h-10 px-4 rounded-xl bg-white text-ink-900 flex items-center">К упражнениям</a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function CircleProgress({ percent, color, label }: { percent: number; color: string; label: string; }) {
  const R = 28;
  const C = 2 * Math.PI * R;
  const dash = (C * Math.max(0, Math.min(100, percent))) / 100;
  return (
    <svg width="96" height="96">
      <circle cx="48" cy="48" r={R} stroke="#E6E8EB" strokeWidth="6" fill="none" />
      <circle cx="48" cy="48" r={R} stroke={color} strokeWidth="6" fill="none" strokeDasharray={`${dash} ${C - dash}`} transform="rotate(-90 48 48)" strokeLinecap="round" />
      <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle" fontSize="18" fill="#111827">{label}</text>
    </svg>
  );
}
