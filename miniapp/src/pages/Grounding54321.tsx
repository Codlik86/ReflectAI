import { useState } from "react";
import BackBar from "../components/BackBar";
import grounding, {
  GroundingStep,
  GroundingStepKey,
  steps as groundingSteps,
} from "../data/grounding.ru";

type StepKey = GroundingStepKey;
type Choice = GroundingStep["choices"][number];
type Step = GroundingStep;

const STEPS: Step[] = grounding.steps ?? groundingSteps;

export default function Grounding54321() {
  const [idx, setIdx] = useState(0);

  // отмеченные элементы по шагам
  const [marked, setMarked] = useState<Record<StepKey, Set<string>>>(() => ({
    see: new Set(),
    touch: new Set(),
    hear: new Set(),
    smell: new Set(),
    taste: new Set(),
  }));

  const step = STEPS[idx];
  const totalSteps = STEPS.length;
  const currentSet = marked[step.key];
  const count = currentSet.size;
  const isLast = idx === totalSteps - 1;

  const toggle = (id: string) => {
    const set = new Set(currentSet);
    set.has(id) ? set.delete(id) : set.add(id);
    setMarked((prev) => ({ ...prev, [step.key]: set }));
  };

  const goPrev = () => setIdx((v) => Math.max(0, v - 1));
  const goNext = () => setIdx((v) => Math.min(totalSteps - 1, v + 1));

  const resetAll = () => {
    setMarked({
      see: new Set(),
      touch: new Set(),
      hear: new Set(),
      smell: new Set(),
      taste: new Set(),
    });
    setIdx(0);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const finish = () => resetAll();

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Заземление 5-4-3-2-1" to="/exercises" />
      <div className="px-5" style={{ height: "clamp(12px,2.2vh,20px)" }} />

      {/* Контент с отступом под BottomBar, но кнопки идут сразу под карточками */}
      <div className="px-5 pb-[calc(env(safe-area-inset-bottom)+92px)]">
        {/* Заголовки */}
        <div className="text-center mb-3">
          <h1 className="text-[22px] font-semibold text-ink-900">{step.title}</h1>
          <p className="mt-1 text-[15px] leading-6 text-ink-700">{step.subtitle}</p>
        </div>

        {/* Список (карточки без обводок и без тени) */}
        <div className="space-y-3">
          {step.choices.map((c: Choice) => {
            const on = currentSet.has(c.id);
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => toggle(c.id)}
                className={`w-full text-left rounded-2xl p-4 transition ${
                  on ? "bg-green-50" : "bg-white"
                }`}
              >
                <div className="flex items-start gap-3">
                  <CustomCheck checked={on} />
                  <div className="flex-1">
                    <div className="text-[16px] text-ink-900">{c.title}</div>
                    {c.hint && (
                      <div className="mt-1 text-[14px] leading-5 text-ink-600">{c.hint}</div>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Кнопки — сразу под карточками, без фоновой панели */}
        <div className="mt-6 flex items-center justify-between">
          <button
            onClick={idx === 0 ? resetAll : goPrev}
            className="px-2 py-1 text-[15px] text-ink-900 underline decoration-black/30 disabled:opacity-30"
            disabled={idx === 0 && count === 0}
          >
            {idx === 0 ? "Сбросить" : "Назад"}
          </button>

          {!isLast ? (
            <button
              onClick={goNext}
              className={`h-10 px-5 rounded-xl font-medium text-white ${
                count > 0 ? "bg-[#22C55E]" : "bg-black/15 text-ink-800"
              }`}
            >
              Далее
            </button>
          ) : (
            <button onClick={finish} className="h-10 px-5 rounded-xl bg-[#22C55E] text-white font-medium">
              Завершить
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ————— кастомный чекбокс ————— */
function CustomCheck({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden
      className={`mt-0.5 inline-flex h-[18px] w-[18px] items-center justify-center rounded-md border ${
        checked ? "border-green-500 bg-green-500" : "border-black/30 bg-white"
      }`}
    >
      {checked ? (
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <path d="M3 6.2 5 8.2 9 3.8" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : null}
    </span>
  );
}
