// src/pages/ThoughtLabeling.tsx
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";

/** Шаги практики: заголовок, подсказка и длительность (сек) */
const STEPS: { title: string; hint: string; seconds: number }[] = [
  {
    title: "Подготовка (бумага и ручка)",
    hint:
      "Найди тихое место. Сядь или ляг удобно. Подготовь лист бумаги и ручку. " +
      "Отключи отвлекающее и на минуту просто устройся поудобнее.",
    seconds: 90,
  },
  {
    title: "Настройка дыхания",
    hint:
      "Дыши спокойно: мягкий вдох через нос, выдох чуть длиннее. " +
      "Отмечай подъём и опускание живота. Мысли пусть проходят мимо.",
    seconds: 60,
  },
  {
    title: "Наблюдай и помечай",
    hint:
      "Смотри на поток мыслей без оценки. Каждой мысли дай метку: «Думаю», «Беспокоюсь», " +
      "«Воспоминание», «Навязчивая», «Катастрофизация», «Обобщение», «Черно-белое». " +
      "Записывай коротко слово-метку — и возвращайся к наблюдению.",
    seconds: 240,
  },
  {
    title: "Уточняющие вопросы",
    hint:
      "Проверь пару заметок вопросами: «Какие факты подтверждают мысль?», " +
      "«Что бы я сказал(а) другу с такой мыслью?». Отмечай, где мысль искажается.",
    seconds: 180,
  },
  {
    title: "Переформулировка",
    hint:
      "Выбери 1–2 записи и переформулируй их более реалистично и мягко. " +
      "Например: «Я могу ошибаться, и это нормально — у меня есть план».",
    seconds: 150,
  },
  {
    title: "Закрепление и завершение",
    hint:
      "Сделай пару спокойных вдохов. Отметь, как сейчас ощущается тело и фон эмоций. " +
      "Поблагодари себя за практику и аккуратно заверши.",
    seconds: 60,
  },
];

type Phase = "intro" | "idle" | "running" | "done";

export default function ThoughtLabeling() {
  const TOTAL = STEPS.length;
  const navigate = useNavigate();

  // экран/фаза
  const [phase, setPhase] = useState<Phase>("intro");

  // индекс текущего шага (1..TOTAL)
  const [stepIdx, setStepIdx] = useState(1);

  // таймер
  const [msLeft, setMsLeft] = useState(0);
  const [progress, setProgress] = useState(0); // 0–100
  const [paused, setPaused] = useState(false);

  // refs для анимации
  const raf = useRef<number | null>(null);
  const endAt = useRef(0);
  const pausedRef = useRef(false);
  const runningRef = useRef(false);

  const stopRaf = () => {
    if (raf.current) cancelAnimationFrame(raf.current);
    raf.current = null;
  };

  const setIdleStep = (idx: number) => {
    const nextIdx = Math.min(Math.max(idx, 1), TOTAL);
    stopRaf();
    setPhase("idle");
    setStepIdx(nextIdx);
    setMsLeft(0);
    setProgress(0);
    setPaused(false);
    pausedRef.current = false;
    runningRef.current = false;
  };

  const begin = () => {
    // со вступительного экрана переходим в «idle» на первом шаге
    setIdleStep(1);
  };

  const startStep = (idx: number) => {
    if (runningRef.current) return;
    const targetIdx = Math.min(Math.max(idx, 1), TOTAL);
    stopRaf();
    const sec = STEPS[targetIdx - 1].seconds;
    const durMs = sec * 1000;
    setStepIdx(targetIdx);
    setPhase("running");
    setMsLeft(durMs);
    setProgress(0);
    setPaused(false);
    pausedRef.current = false;
    runningRef.current = true;
    endAt.current = performance.now() + durMs;
    tick(durMs, () => {
      runningRef.current = false;
      if (targetIdx < TOTAL) {
        setIdleStep(targetIdx + 1);
      } else {
        setPhase("done");
        setMsLeft(0);
        setProgress(0);
      }
    });
  };

  const tick = (totalMs: number, onFinish: () => void) => {
    stopRaf();
    const loop = () => {
      if (!runningRef.current) return; // перестраховка
      if (pausedRef.current) {
        raf.current = requestAnimationFrame(loop);
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
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
  };

  const onPauseResume = () => {
    if (phase !== "running" || !runningRef.current) return;
    pausedRef.current = !pausedRef.current;
    setPaused(pausedRef.current);
    if (!pausedRef.current) {
      endAt.current = performance.now() + msLeft; // продолжили: переносим дедлайн
    }
  };

  const onStop = () => {
    runningRef.current = false;
    setIdleStep(stepIdx);
  };

  // навигация снизу
  const goStart = () => {
    setIdleStep(1);
  };
  const goPrev = () => {
    if (stepIdx === 1) return;
    setIdleStep(stepIdx - 1);
  };
  const goNext = () => {
    if (stepIdx === TOTAL) return;
    setIdleStep(stepIdx + 1);
  };

  const mm = Math.floor(msLeft / 1000 / 60)
    .toString()
    .padStart(2, "0");
  const ss = Math.floor((msLeft / 1000) % 60)
    .toString()
    .padStart(2, "0");

  const current = STEPS[stepIdx - 1];

  // helper для тач-фикса
  const onPD = (e: React.PointerEvent) => {
    if ((e as any).pointerType === "touch") e.preventDefault();
  };

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Маркировка мыслей" to="/exercises" />
      <div className="px-5" style={{ height: "clamp(12px,2.2vh,20px)" }} />

      <div className="px-5 space-y-4" style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}>
        {/* ---- Интро ---- */}
        {phase === "intro" && (
          <div className="rounded-3xl bg-white p-5 space-y-4">
            <div className="text-[18px] font-medium text-ink-900">Спокойная практика на 10–12 минут</div>
            <div className="text-[16px] leading-7 text-ink-700">
              «Маркировка мыслей» помогает замечать автоматические мысли, создавать небольшую
              дистанцию и возвращать себе выбор. Мы наблюдаем поток без осуждения, кратко
              помечаем мысли по типам и — при желании — мягко переформулируем.
            </div>
            <div className="text-[16px] leading-7 text-ink-700">
              Подготовь бумагу и ручку: записи короткие (одно-двухсловные метки). Если мысль
              ускользает — ничего страшного, просто возвращай внимание.
            </div>
            <div className="flex items-center justify-center">
              <button
                onPointerDown={onPD}
                style={{ touchAction: "manipulation" }}
                onClick={begin}
                className="btn btn-primary"
              >
                Начать
              </button>
            </div>
          </div>
        )}

        {/* ---- Основной экран: idle / running ---- */}
        {(phase === "idle" || phase === "running") && (
          <div className="rounded-3xl bg-white p-5">
            <div className="flex items-center justify-between">
              <div className="text-[16px] text-ink-600">Шаг {stepIdx}/{TOTAL}</div>
              <div className="text-[16px] text-ink-600">{phase === "idle" ? "00:00" : `${mm}:${ss}`}</div>
            </div>

            <div className="mt-3 text-center">
              <div className="text-[22px] font-semibold text-ink-900">{current.title}</div>
            </div>

            <div className="mt-4 flex justify-center">
              <CircleProgress
                percent={phase === "idle" ? 0 : progress}
                color={phase === "running" ? "#3B82F6" : "#22C55E"}
                label={phase === "idle" ? "0" : Math.ceil(msLeft / 1000).toString()}
              />
            </div>

            <div className="mt-4 text-center text-[15px] leading-7 text-ink-700">
              {current.hint}
            </div>

            <div className="mt-5 flex items-center justify-center gap-3">
              {phase === "idle" ? (
                <button
                  onPointerDown={onPD}
                  style={{ touchAction: "manipulation" }}
                  onClick={() => startStep(stepIdx)}
                  className="btn btn-primary"
                >
                  Старт
                </button>
              ) : (
                <button
                  onPointerDown={onPD}
                  style={{ touchAction: "manipulation" }}
                  onClick={onPauseResume}
                  className="btn btn-primary"
                >
                  {paused ? "Продолжить" : "Пауза"}
                </button>
              )}
              <button
                onPointerDown={onPD}
                style={{ touchAction: "manipulation" }}
                onClick={onStop}
                className="btn btn-stop"
              >
                Остановить
              </button>
            </div>

            <div className="mt-5 pt-4 border-t border-black/5">
              <div className="flex items-center justify-center gap-2">
                <button
                  onPointerDown={onPD}
                  style={{ touchAction: "manipulation" }}
                  onClick={goStart}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80"
                >
                  Сначала
                </button>
                <button
                  onPointerDown={onPD}
                  style={{ touchAction: "manipulation" }}
                  onClick={goPrev}
                  disabled={stepIdx === 1}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                >
                  Назад
                </button>
                <button
                  onPointerDown={onPD}
                  style={{ touchAction: "manipulation" }}
                  onClick={goNext}
                  disabled={stepIdx === TOTAL}
                  className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 hover:opacity-80 disabled:opacity-30"
                >
                  Вперёд
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ---- Финал ---- */}
        {phase === "done" && (
          <div className="rounded-3xl border border-green-200 bg-green-50 p-6 text-center">
            <div className="text-[18px] font-semibold text-ink-900 mb-1">Готово — отметки сделаны</div>
            <div className="text-[15px] text-ink-700">
              Посмотри на записи: какие метки встречались чаще? Как изменилась интенсивность чувств?
            </div>
            <div className="mt-4 flex items-center justify-center gap-3">
              <button
                onPointerDown={onPD}
                style={{ touchAction: "manipulation" }}
                onClick={() => setIdleStep(1)}
                className="h-10 px-4 rounded-xl bg-[#FFA66B] text-white font-medium"
              >
                Ещё раз
              </button>
              <button
                type="button"
                onPointerDown={(e) => { if ((e as any).pointerType === "touch") e.preventDefault(); }}
                onClick={() => navigate("/exercises")}
                className="h-10 px-4 rounded-xl bg-white text-ink-900 flex items-center"
                style={{ touchAction: "manipulation" }}
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

/* --- Круговой индикатор (тот же, что в Breath46) --- */
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
