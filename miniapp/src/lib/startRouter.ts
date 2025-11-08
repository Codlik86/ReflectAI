// src/lib/startRouter.ts
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { getStartParam } from "./telegram";

// Куда вести по коду
const NAV_MAP: Record<string, string> = {
  // медитации
  "med": "/meditations",
  "med-sleep": "/meditations", // при желании можно сделать /meditations/:id

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

/** Хук: единожды читает start-код и, если нужно, делает навигацию */
export function useStartRouter() {
  const navigate = useNavigate();
  const done = useRef(false);

  useEffect(() => {
    if (done.current) return;

    const raw = getStartParam();
    if (!raw) return;

    const key = normalize(raw);
    const dest = NAV_MAP[key];

    if (dest) {
      navigate(dest, { replace: true });
      done.current = true;
      return;
    }

    // Если это рекламный код — можно просто оставить на главной,
    // но добавить query (?ad=B01a) — пригодится для сбора атрибуции
    if (looksLikeAdCode(key)) {
      const url = new URL(window.location.href);
      url.searchParams.set("ad", key);
      navigate(`${url.pathname}${url.search}`, { replace: true });
      done.current = true;
    }
  }, [navigate]);
}
