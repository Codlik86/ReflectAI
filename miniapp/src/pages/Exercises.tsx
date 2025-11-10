// src/pages/Exercises.tsx
import * as React from "react";
import { useState, useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";
import data from "../data/exercises.ru";
import type { AAPayload, AAExercise, AAStep } from "../lib/aa.types";
import { ensureAccess } from "../lib/guard";

export default function Exercises() {
  const navigate = useNavigate();
  const payload: AAPayload = data;
  const [active, setActive] = useState<AAExercise | null>(null);

  // Проверяем доступ один раз за маунт
  const checkedRef = useRef(false);
  useEffect(() => {
    if (checkedRef.current) return;
    checkedRef.current = true;
    let cancelled = false;

    (async () => {
      try {
        const snap = await ensureAccess(false); // без автозапуска триала
        if (!cancelled && !snap.has_access) {
          navigate(`/paywall?from=${encodeURIComponent("/exercises")}`, { replace: true });
        }
      } catch {
        if (!cancelled) {
          navigate(`/paywall?from=${encodeURIComponent("/exercises")}`, { replace: true });
        }
      }
    })();

    return () => { cancelled = true; };
  }, [navigate]);

  // ====== Список упражнений ======
  if (!active) {
    return (
      <div className="min-h-dvh flex flex-col">
        <BackBar title="Упражнения" to="/" />
        <div style={{ height: "clamp(20px, 3.8vh, 32px)" }} />
        <div
          className="px-5 space-y-5"
          style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}
        >
          {payload.categories.map((cat, idx) => (
            <section key={cat.id} className={`space-y-3 ${idx === 0 ? "" : "mt-7"}`}>
              <h3 className="text-[18px] leading-6 font-semibold text-ink-900 px-1">
                {cat.title}
              </h3>

              {cat.items.map((item) => {
                const Card = (
                  <div className="card-btn">
                    <div>
                      <div className="card-title text-[20px] leading-7">{item.title}</div>
                      {item.subtitle && (
                        <div className="text-[14px] leading-6 text-ink-500 mt-1">
                          {item.subtitle}
                        </div>
                      )}
                      {item.duration && (
                        <div className="mt-1 flex items-center gap-2 text-[14px] leading-5 text-ink-500">
                          <span
                            aria-hidden
                            className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400 relative top-px"
                          />
                          <span>{item.duration}</span>
                        </div>
                      )}
                    </div>
                  </div>
                );

                return item.route ? (
                  <Link key={item.id} to={item.route} className="block">
                    {Card}
                  </Link>
                ) : (
                  <button
                    key={item.id}
                    className="w-full text-left"
                    onClick={() => setActive(item)}
                  >
                    {Card}
                  </button>
                );
              })}
            </section>
          ))}
        </div>
      </div>
    );
  }

  // ====== Детальная карточка ======
  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title={active.title} onBack={() => setActive(null)} />
      <div style={{ height: "clamp(12px, 2.2vh, 20px)" }} />

      <div
        className="px-5 space-y-5"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}
      >
        {active.subtitle && (
          <div className="px-0">
            <div className="text-[16px] leading-6 text-ink-700">{active.subtitle}</div>
          </div>
        )}

        {active.steps.map((s, i) =>
          s.type === "text" ? (
            <div key={i} className="px-0">
              <TextBlock html={s.html} />
            </div>
          ) : (
            <div key={i} className="card-btn" style={{ height: "auto", padding: 16 }}>
              <StepRenderer step={s} />
            </div>
          )
        )}

        <button
          onClick={() => setActive(null)}
          className="w-full h-12 rounded-3xl bg-white flex items-center justify-center text-[16px] font-medium text-ink-700"
        >
          К списку упражнений
        </button>
      </div>
    </div>
  );
}

/* ===== мини-виджеты ===== */

function TextBlock({ html }: { html: string }) {
  return (
    <div
      className="text-[16px] leading-6 text-ink-800 px-0"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function ProgressBar({ value }: { value: number }) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className="mt-2 h-2 w-full rounded-full bg-black/10 overflow-hidden">
      <div
        className="h-full rounded-full"
        style={{
          width: `${(v * 100).toFixed(2)}%`,
          transition: "width 120ms linear",
          background: "linear-gradient(90deg, rgba(255,209,150,.9), rgba(255,191,220,.9))",
        }}
      />
    </div>
  );
}

/* ===== рендер шагов ===== */

function StepRenderer({ step }: { step: AAStep }) {
  switch (step.type) {
    case "list":
      return (
        <ul className="list-disc pl-5 space-y-1 text-[15px] leading-6 text-ink-800">
          {step.items.map((li, idx) => (
            <li key={idx} dangerouslySetInnerHTML={{ __html: li }} />
          ))}
        </ul>
      );
    case "timer":
      return <Timer seconds={step.seconds} label={step.label} />;
    case "breath":
      return (
        <BreathWidget
          inhale={step.inhale}
          hold={step.hold ?? 0}
          exhale={step.exhale}
          hold2={(step as any).hold2 ?? 0}
          cycles={step.cycles ?? 5}
          label={step.label}
        />
      );
    default:
      return null;
  }
}

/* ===== TIMER ===== */

function Timer({ seconds, label }: { seconds: number; label?: string }) {
  const [running, setRunning] = React.useState(false);
  const runningRef = React.useRef(false);
  const [left, setLeft] = React.useState(seconds);
  const startAt = React.useRef<number | null>(null);
  const rafRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    runningRef.current = running;
  }, [running]);

  const cancel = () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  };

  const reset = React.useCallback(() => {
    setRunning(false);
    runningRef.current = false;
    setLeft(seconds);
    startAt.current = null;
    cancel();
  }, [seconds]);

  React.useEffect(() => reset(), [seconds, reset]);

  const tick = (t: number) => {
    if (!runningRef.current) return;
    if (!startAt.current) startAt.current = t;
    const elapsed = (t - startAt.current) / 1000;
    const remain = Math.max(0, seconds - elapsed);
    setLeft(remain);
    if (remain <= 0) {
      reset();
      return;
    }
    rafRef.current = requestAnimationFrame(tick);
  };

  const start = () => {
    reset();
    setRunning(true);
    runningRef.current = true;
    rafRef.current = requestAnimationFrame(tick);
  };
  const stop = () => reset();

  const mm = Math.floor(left / 60).toString().padStart(2, "0");
  const ss = Math.floor(left % 60).toString().padStart(2, "0");
  const progress = ((seconds - left) / seconds) * 100;

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[16px] text-ink-700">{label ?? "Таймер"}</div>
        <div className="text-[16px] font-medium tabular-nums">
          {mm}:{ss}
        </div>
      </div>

      <div className="h-2 rounded-full bg-black/5 overflow-hidden">
        <div
          className="h-full"
          style={{
            width: `${progress}%`,
            transition: running ? "width .2s linear" : undefined,
            background: "linear-gradient(90deg,#C7F0FF,#F9D7FF)",
          }}
        />
      </div>

      <div className="mt-3 flex justify-center">
        {!running ? (
          <button onClick={start} className="btn btn-primary">
            Начать
          </button>
        ) : (
          <button onClick={stop} className="btn btn-stop">
            Остановить
          </button>
        )}
      </div>
    </div>
  );
}

/* ===== BREATH ===== */

function BreathWidget({
  inhale,
  hold = 0,
  exhale,
  hold2 = 0,
  cycles = 3,
  label,
}: {
  inhale: number;
  hold?: number;
  exhale: number;
  hold2?: number;
  cycles?: number;
  label?: string;
}) {
  type Phase = "inhale" | "hold" | "exhale" | "hold2";
  const [running, setRunning] = React.useState(false);
  const runningRef = React.useRef(false);
  const [phase, setPhase] = React.useState<Phase>("inhale");
  const [cycle, setCycle] = React.useState(1);
  const [elapsed, setElapsed] = React.useState(0);
  const rafRef = React.useRef<number | null>(null);
  const lastT = React.useRef<number | null>(null);

  React.useEffect(() => {
    runningRef.current = running;
  }, [running]);

  const phaseDur = React.useMemo(
    () => (phase === "inhale" ? inhale : phase === "hold" ? hold : phase === "exhale" ? exhale : hold2),
    [phase, inhale, hold, exhale, hold2]
  );

  const cancel = () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  };

  const reset = React.useCallback(() => {
    setRunning(false);
    runningRef.current = false;
    setPhase("inhale");
    setCycle(1);
    setElapsed(0);
    lastT.current = null;
    cancel();
  }, []);

  const total = inhale + (hold || 0) + exhale + (hold2 || 0);
  const offsets = { inhale: 0, hold: inhale, exhale: inhale + hold, hold2: inhale + hold + exhale } as const;
  const cycleProgress = (offsets[phase] + Math.min(elapsed, phaseDur)) / (total || 1);
  const overallProgress = ((cycle - 1) + cycleProgress) / cycles;

  const step = (t: number) => {
    if (!runningRef.current) return;
    if (!lastT.current) lastT.current = t;
    const dt = (t - lastT.current) / 1000;
    lastT.current = t;

    setElapsed((e) => {
      const next = e + dt;
      if (next >= (phaseDur || 0)) {
        setElapsed(0);
        setPhase((p) => {
          if (p === "inhale") return hold > 0 ? "hold" : "exhale";
          if (p === "hold") return "exhale";
          if (p === "exhale") return hold2 > 0 ? "hold2" : advanceCycle();
          if (p === "hold2") return advanceCycle();
          return "inhale";
        });
        return 0;
      }
      return next;
    });

    rafRef.current = requestAnimationFrame(step);
  };

  function advanceCycle(): Phase {
    let finished = false;
    setCycle((c) => {
      const nc = c + 1;
      if (nc > cycles) { finished = true; return c; }
      return nc;
    });
    if (finished) reset();
    return "inhale";
  }

  const start = () => {
    reset();
    setRunning(true);
    runningRef.current = true;
    rafRef.current = requestAnimationFrame(step);
  };
  const stop = () => reset();

  return (
    <div className="w-full">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[16px] text-ink-700">{label ?? "Дыхательный цикл"}</div>
        <div className="text-[14px] text-ink-500">цикл {Math.min(cycle, cycles)}/{cycles}</div>
      </div>

      <div className="h-2 rounded-full bg-black/5 overflow-hidden">
        <div
          className="h-full"
          style={{
            width: `${overallProgress * 100}%`,
            transition: running ? "width .2s linear" : undefined,
            background: "linear-gradient(90deg,#C7F0FF,#F9D7FF)",
          }}
        />
      </div>

      <div className="mt-2 text-[14px] text-ink-600">
        Фаза: {phase === "inhale" ? "Вдох" : phase === "hold" ? "Пауза" : phase === "exhale" ? "Выдох" : "Пауза"}.
        Осталось: {Math.max(0, Math.ceil((phaseDur || 0) - elapsed))} сек.
      </div>

      <div className="mt-3 flex justify-center">
        {!running ? (
          <button onClick={start} className="btn btn-primary">Начать</button>
        ) : (
          <button onClick={stop} className="btn btn-stop">Остановить</button>
        )}
      </div>
    </div>
  );
}
