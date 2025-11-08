// src/lib/aa.types.ts

export type AAStep =
  | { type: "text"; html: string }
  | { type: "list"; items: string[] }
  | { type: "timer"; seconds: number; label?: string }
  | {
      type: "breath";
      label?: string;
      inhale: number;
      hold?: number;
      exhale: number;
      hold2?: number;     // ← вторая пауза (для 4-4-4-4)
      cycles?: number;
    };

export type AAExercise = {
  id: string;
  title: string;
  subtitle?: string;
  duration?: string;
  steps: AAStep[];
  route?: string;         // если есть — карточка ведёт на отдельную страницу
};

export type AAPayload = {
  // ↓ новые опциональные поля — чтобы не ругался TS
  locale?: string;
  updatedAt?: string;

  categories: {
    id: string;
    title: string;
    items: AAExercise[];
  }[];
};
