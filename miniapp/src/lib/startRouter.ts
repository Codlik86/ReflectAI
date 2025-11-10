// src/lib/startRouter.ts
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { getStartParam } from "./telegram";

// Куда вести по коду
const NAV_MAP: Record<string, string> = {
  // медитации
  "med": "/meditations",
  "med-sleep": "/meditations",

  // упражнения
  "ex": "/exercises",
  "ex-478": "/exercises/breath-478",
  "ex-4444": "/exercises/breath-4444",
  "ex-46": "/exercises/breath-46",
  "ex-333": "/exercises/breath-333",
  "ex-pmr": "/exercises/pmr",
  "ex-ground": "/exercises/grounding",
  "ex-thoughts": "/exercises/thought-labeling",
};

// очень простой признак «это рекламный код, а не навигация» (B01a, B05b…)
function looksLikeAdCode(code: string) {
  return /^[A-Z]\d{2}[ab]$/i.test(code);
}

function normalize(code: string) {
  return code.trim();
}

const START_ROUTED_KEY = "START_ROUTED_CODE";

/** Хук: единожды читает start-код и, если нужно, делает навигацию */
export function useStartRouter() {
  const navigate = useNavigate();
  const done = useRef(false);

  useEffect(() => {
    if (done.current) return;

    // Анти-дубль для StrictMode и повторных маунтов
    const already = sessionStorage.getItem(START_ROUTED_KEY);
    if (already === "1") {
      done.current = true;
      return;
    }

    const raw = getStartParam();
    if (!raw) return;

    const key = normalize(raw);
    const dest = NAV_MAP[key];

    // Помечаем ВПЕРЕД, чтобы навигация/ремонт не вызвали повтор
    const markDone = () => {
      done.current = true;
      sessionStorage.setItem(START_ROUTED_KEY, "1");
    };

    if (dest) {
      markDone();
      navigate(dest, { replace: true });
      return;
    }

    // Если это рекламный код — оставляем на текущем экране,
    // но аккуратно добавляем ?ad=... без лишних перерисовок
    if (looksLikeAdCode(key)) {
      const url = new URL(window.location.href);
      if (url.searchParams.get("ad") !== key) {
        url.searchParams.set("ad", key);
        // replace без смены роута — минимальная мутация истории
        window.history.replaceState({}, "", `${url.pathname}${url.search}`);
      }
      markDone();
    }
  }, [navigate]);
}
