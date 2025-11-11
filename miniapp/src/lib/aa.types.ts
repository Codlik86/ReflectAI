// src/lib/aa.types.ts
export type AAStep =
  | { type: "text"; html: string }
  | { type: "list"; items: string[] }
  | { type: "timer"; seconds: number; label?: string }
  | {
      type: "breath";
      inhale: number;
      hold?: number;
      exhale: number;
      hold2?: number;
      cycles?: number;
      label?: string;
    };

export type AAExercise = {
  id: string;
  title: string;
  subtitle?: string;
  duration?: string;
  steps: AAStep[];
  /** Если задан — карточка ведёт на отдельный роут, а не на «деталку» */
  route?: string;
};

export type AACategory = {
  id: string;
  title: string;
  items: AAExercise[];
};

export type AAPayload = {
  categories: AACategory[];
};
