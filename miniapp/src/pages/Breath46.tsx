import { useRef, useState } from "react";
import BackBar from "../components/BackBar";

type Phase = "intro" | "idle" | "inhale" | "exhale" | "done";

const INHALE_SEC = 4;
const EXHALE_SEC = 6;
const TOTAL_CYCLES = 12;

export default function Breath46() {
  const [phase, setPhase] = useState<Phase>("intro");
  const [cycle, setCycle] = useState(1);
  const [msLeft, setMsLeft] = useState(0);
  const [progress, setProgress] = useState(0); // 0–100 для круга
  const [paused, setPaused] = useState(false);

  // time refs
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

  const begin = () => {
    resetAll(); // на экран «idle», без автозапуска
  };

  const startPhase = (next: Phase) => {
    setPhase(next);
    runningRef.current = true;
    const durMs = (next === "inhale" ? INHALE_SEC : EXHALE_SEC) * 1000;
    currentDurMs.current = durMs;
    endAt.current = performance.now() + durMs;
    tick(durMs, () => {
      if (next === "inhale") {
        startPhase("exhale");
      } else {
        // закончился выдох — перейти к следующему циклу или финиш
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

  const start = () => {
    if (phase === "idle") startPhase("inhale");
  };

  const pauseToggle = () => {
    if (phase !== "inhale" && phase !== "exhale") return;
    pausedRef.current = !pausedRef.current;
    setPaused(pausedRef.current);
    if (!pausedRef.current) {
      // «распаузили»: продли дедлайн на время «стояния»
      endAt.current = performance.now() + msLeft;
    }
  };

  const stop = () => resetAll();

  // Кнопки навигации снизу (как в PMR)
  const goStart = () => {
    resetAll();
  };
  const goPrev = () => {
    const prev = Math.max(1, cycle - 1);
    resetAll();
    setCycle(prev);
  };
  const goNext = () => {
    const next = Math.min(TOTAL_CYCLES, cycle + 1);
    resetAll();
    setCycle(next);
  };

  const mm = Math.floor(msLeft / 1000 / 60)
    .toString()
    .padStart(2, "0");
  const ss = Math.floor((msLeft / 1000) % 60)
    .toString()
    .padStart(2, "0");

  const phaseBadge =
    phase === "inhale" ? (
      <span className="inline-flex items-center rounded-full bg-blue-50 text-blue-500 text-[12px] px-2 py-1 font-medium">
        Вдох
      </span>
    ) : phase === "exhale" ? (
      <span className="inline-flex items-center rounded-full bg-green-50 text-green-700 text-[12px] px-2 py-1 font-medium">
        Выдох
      </span>
    ) : null;

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Дыхание 4–6" to="/exercises" />
      <div className="px-5" style={{ height: "clamp(12px,2.2vh,20px)" }} />

      <div className="px-5 space-y-4" style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}>
        {/* Интро */}
        {phase === "intro" && (
          <div className="rounded-3xl bg-white p-5 space-y-4">
            <div className="text-[18px] font-medium text-ink-900">Быстрое дыхание за 2–3 минуты</div>
            <div className="text-[16px] leading-7 text-ink-700">
              Спокойный вдох 4 секунды и мягкий выдох 6 секунд. Всего 12 циклов.
            </div>
            <div className="flex items-center justify-center">
              <button onClick={begin} className="btn btn-primary">Начать</button>
            </div>
          </div>
        )}

        {/* Основной экран */}
        {(phase === "idle" || phase === "inhale" || phase === "exhale") && (
          <div className="rounded-3xl bg-white p-5">
            <div className="flex items-center justify-between">
              <div className="text-[16px] text-ink-600">Цикл {Math.min(cycle, TOTAL_CYCLES)}/{TOTAL_CYCLES}</div>
              <div className="text-[16px] text-ink-600">{phase === "idle" ? "00:00" : `${mm}:${ss}`}</div>
            </div>

            {/* бейдж текущей фазы */}
            <div className="mt-2 text-center">{phaseBadge}</div>

            {/* круглый индикатор */}
            <div className="mt-4 flex justify-center">
              <CircleProgress
                percent={phase === "idle" ? 0 : progress}
                color={phase === "inhale" ? "#3B82F6" : "#22C55E"}
                label={phase === "idle" ? "0" : Math.ceil(msLeft / 1000).toString()}
              />
            </div>

            {/* подсказка */}
            <div className="mt-3 text-center text-[14px] text-ink-600">
              {phase === "inhale" && "Вдыхай носом, расширяя живот."}
              {phase === "exhale" && "Выдыхай мягко и ровно, чуть дольше, чем вдох."}
              {phase === "idle" && "Готов к старту: вдох 4 сек → выдох 6 сек."}
            </div>

            {/* управление */}
            <div className="mt-5 flex items-center justify-center gap-3">
              {phase === "idle" ? (
                <button onClick={start} className="btn btn-primary">Старт</button>
              ) : (
                <button
                  onClick={pauseToggle}
                  className={`btn ${paused ? "btn-primary" : "btn-primary"}`} /* пауза/продолжить оставляем оранжевой */
                >
                  {paused ? "Продолжить" : "Пауза"}
                </button>
              )}
              <button onClick={stop} className="btn btn-stop">Остановить</button>
            </div>

            {/* нижняя навигация (необязательная, но как в PMR) */}
            <div className="mt-5 pt-4 border-t border-black/5">
              <div className="flex items-center justify-center gap-2">
                <button
                  onClick={goStart}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80"
                >
                  Сначала
                </button>
                <button
                  onClick={goPrev}
                  disabled={cycle === 1}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                >
                  Назад
                </button>
                <button
                  onClick={goNext}
                  disabled={cycle === TOTAL_CYCLES}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
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
            <div className="text-[18px] font-semibold text-ink-900 mb-1">Готово — дыхание выровнялось</div>
            <div className="text-[15px] text-ink-700">Можно повторить ещё раз или вернуться к списку.</div>
            <div className="mt-4 flex items-center justify-center gap-3">
              <button
                onClick={() => {
                  setPhase("idle");
                  setCycle(1);
                }}
                className="h-10 px-4 rounded-xl bg-[#FFA66B] text-white font-medium"
              >
                Ещё раз
              </button>
              <a href="/exercises" className="h-10 px-4 rounded-xl bg-white text-ink-900 flex items-center">
                К упражнениям
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ===== круговой индикатор (как в PMR) ===== */
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
  const dash = (C * Math.max(0, Math.min(100, percent))) / 100;

  return (
    <svg width="96" height="96">
      <circle cx="48" cy="48" r={R} stroke="#E6E8EB" strokeWidth="6" fill="none" />
      <circle
        cx="48"
        cy="48"
        r={R}
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
