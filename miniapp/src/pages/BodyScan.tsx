// src/pages/BodyScan.tsx
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";
import body from "../data/bodyscan.ru";

type Phase = "intro" | "idle" | "scan" | "done";

export default function BodyScan() {
  const navigate = useNavigate();

  const groups = body.groups;
  const defSec = body.defaultDurationSec;

  const [idx, setIdx] = useState(0);
  const idxRef = useRef(0);

  const [phase, setPhase] = useState<Phase>("intro");
  const [msLeft, setMsLeft] = useState(0);
  const [progress, setProgress] = useState(0);

  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);

  // RAF-таймер
  const rafId = useRef<number | null>(null);
  const endAt = useRef<number>(0);
  const pauseStartedAt = useRef<number | null>(null);

  const stopRaf = () => {
    if (rafId.current) cancelAnimationFrame(rafId.current);
    rafId.current = null;
  };

  const getDurationMs = () =>
    (groups[idxRef.current]?.seconds ?? defSec) * 1000;

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

  const begin = () => {
    stopRaf();
    idxRef.current = 0;
    setIdx(0);
    setPhase("idle"); // явный старт по кнопке
    setProgress(0);
    setMsLeft(0);
    setPaused(false);
    pausedRef.current = false;
  };

  const start = () => {
    if (phase !== "idle") return;
    setPhase("scan");
    const dur = getDurationMs();
    endAt.current = performance.now() + dur;
    tick(dur, () => {
      // авто-переход к следующей области
      if (idxRef.current < groups.length - 1) {
        setProgress(0);
        setMsLeft(0);
        setPaused(false);
        pausedRef.current = false;
        setIdx((v) => {
          const nv = v + 1;
          idxRef.current = nv;
          return nv;
        });
        requestAnimationFrame(start); // авто-старт следующего блока
      } else {
        setPhase("done");
      }
    });
  };

  const pauseToggle = () => {
    if (phase !== "scan") return;
    if (!pausedRef.current) {
      setPaused(true);
      pausedRef.current = true;
      pauseStartedAt.current = performance.now();
    } else {
      setPaused(false);
      pausedRef.current = false;
      if (pauseStartedAt.current != null) {
        const delta = performance.now() - pauseStartedAt.current;
        endAt.current += delta; // продлеваем таймер на длительность паузы
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
  };

  // Навигация по группам
  const goStart = () => {
    stopRaf();
    idxRef.current = 0;
    setIdx(0);
    setPhase("idle");
    setProgress(0);
    setMsLeft(0);
    setPaused(false);
    pausedRef.current = false;
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
  };

  useEffect(() => () => stopRaf(), []);

  const g = groups[idx];
  const mm = Math.floor(msLeft / 1000 / 60).toString().padStart(2, "0");
  const ss = Math.floor((msLeft / 1000) % 60).toString().padStart(2, "0");

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar
        title="Боди-скан"
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
            <div className="text-[18px] font-medium text-ink-900">Сканирование тела</div>
            <div className="text-[16px] leading-7 text-ink-700">
              Займи удобное положение и мягко пройдись вниманием по телу. 
              Ничего не исправляй — просто замечай ощущения и отпускай напряжение на выдохе.
            </div>
            <ul className="rounded-2xl bg-white border border-black/5 p-4 space-y-2 text-[15px]">
              <li>Найди тихое место, выключи «Не беспокоить».</li>
              <li>Сделай пару спокойных вдохов-выдохов.</li>
              <li>Двигайся сверху вниз: голова → плечи → руки → корпус → таз → ноги → всё тело.</li>
            </ul>
            <div className="flex items-center justify-center">
              <button onClick={begin} className="btn btn-primary">Начать</button>
            </div>
          </div>
        )}

        {/* Основной экран */}
        {(phase === "idle" || phase === "scan") && (
          <div className="rounded-3xl bg-white p-5">
            <div className="flex items-center justify-between text-[16px] text-ink-600">
              <div>
                Область {idx + 1} из {groups.length}
              </div>
              <div>{phase === "idle" ? "00:00" : `${mm}:${ss}`}</div>
            </div>

            {/* Название области */}
            <div className="mt-3 text-center">
              <div className="text-[20px] font-semibold text-ink-900">{g.name}</div>
            </div>

            {/* Индикатор */}
            <div className="mt-4 flex justify-center">
              <CircleProgress
                percent={phase === "idle" ? 0 : progress}
                color="#22C55E"
                label={phase === "idle" ? "0" : Math.ceil(msLeft / 1000).toString()}
              />
            </div>

            {/* Подсказки */}
            <div className="mt-4 rounded-2xl border border-black/5 p-3">
              <CueList steps={g.cue} />
            </div>

            {/* Кнопки управления */}
            <div className="mt-5 flex items-center justify-center gap-3">
              {phase === "idle" ? (
                <button onClick={start} className="btn btn-primary">Старт</button>
              ) : (
                <button
                  onClick={pauseToggle}
                  className={`btn ${paused ? "btn-neutral" : "btn-primary"}`}
                >
                  {paused ? "Продолжить" : "Пауза"}
                </button>
              )}
              <button onClick={stopAll} className="btn btn-stop">Остановить</button>
            </div>

            {/* Нижняя навигация */}
            <div className="mt-5 pt-4 border-t border-black/5">
              <div className="flex items-center justify-center gap-2">
                <button onClick={goStart} className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80">
                  Сначала
                </button>
                <button
                  onClick={goPrev}
                  disabled={idx === 0}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                >
                  Назад
                </button>
                <button
                  onClick={goNext}
                  disabled={idx === groups.length - 1}
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
            <div className="text-[18px] font-semibold text-ink-900 mb-1">Готово</div>
            <div className="text-[15px] text-ink-700">
              Ощути тело целиком, сделай медленный выдох. Можно повторить на отдельных зонах.
            </div>
            <div className="mt-4 flex items-center justify-center gap-3">
              <button onClick={begin} className="btn btn-primary">Ещё раз</button>
              <button onClick={() => navigate("/exercises")} className="h-10 px-4 rounded-xl bg-white text-ink-900">
                К списку упражнений
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* helpers */

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

function CueList({ steps }: { steps: string[] }) {
  return (
    <ol className="space-y-2 text-sm">
      {steps.map((s, i) => (
        <li key={i} className="flex items-start gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-green-200 text-green-800 text-[12px] font-bold">
            {i + 1}
          </span>
          <span className="text-[14px] leading-5 text-ink-900">{s}</span>
        </li>
      ))}
    </ol>
  );
}
