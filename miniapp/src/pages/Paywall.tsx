// src/pages/Paywall.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import BackBar from "../components/BackBar";
import { ensureAccess } from "../lib/guard";
import { getTelegramUserId } from "../lib/telegram";

/** Открыть чат бота (tg:// → https фоллбек) и мягко закрыть WebView */
function openInBot(startParam?: string) {
  const bot = (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";
  const wa = (window as any)?.Telegram?.WebApp;

  const start = startParam ? `?start=${encodeURIComponent(startParam)}` : "";
  const tgDeep = `tg://resolve?domain=${encodeURIComponent(bot)}${start}`;
  const httpsUrl = `https://t.me/${bot}${start}`;

  try {
    if (wa?.openTelegramLink) wa.openTelegramLink(tgDeep);
    else if (typeof window !== "undefined") window.location.href = tgDeep;
  } catch {}
  try {
    if (wa?.openTelegramLink) wa.openTelegramLink(httpsUrl);
    else window.open(httpsUrl, "_blank", "noopener,noreferrer");
  } catch {
    window.location.href = httpsUrl;
  }
  setTimeout(() => {
    try { wa?.close?.(); } catch {}
  }, 120);
}

type View = "loading" | "has-access" | "needs-policy" | "pre-trial" | "expired";

type StatusDTO = {
  has_access?: boolean;
  status?: "active" | "trial" | "none" | null;
  until?: string | null;
  plan?: "week" | "month" | "quarter" | "year" | null;
  is_auto_renew?: boolean | null;
  needs_policy?: boolean | null;           // может прийти с payments/status (если добавлено)

  // Для корректного различения pre-trial vs expired (канон из /api/access/status)
  trial_ever?: boolean;                    // был ли когда-либо триал или подписка
  trial_started_at?: string | null;
  trial_expires_at?: string | null;
  subscription_until?: string | null;
};

const nowMs = () => Date.now();

function normalizeBase(s?: string | null) {
  if (!s) return "";
  return s.replace(/\/+$/, "");
}
const API_BASE =
  normalizeBase((import.meta as any)?.env?.VITE_API_BASE) ||
  normalizeBase(window.localStorage.getItem("VITE_API_BASE")) ||
  "";
const apiUrl = (path: string) => (API_BASE ? `${API_BASE}${path}` : path);

export default function Paywall() {
  const nav = useNavigate();
  const loc = useLocation();
  const redirectedRef = useRef(false);

  const params = new URLSearchParams(loc.search);
  const from = params.get("from") || "/";

  const [status, setStatus] = useState<StatusDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const snap = await ensureAccess({ startTrial: false });
        if (!cancelled && snap.has_access) {
          if (!redirectedRef.current) {
            redirectedRef.current = true;
            setTimeout(() => nav(from, { replace: true }), 10);
          }
          return;
        }
      } catch {
        // игнор
      }

      try {
        setLoading(true);
        setErr(null);
        const tg = getTelegramUserId();

        // 1) основной статус из /api/payments/status
        const u1 = new URL(apiUrl("/api/payments/status"), window.location.origin);
        if (tg) u1.searchParams.set("tg_user_id", String(tg));
        const r1 = await fetch(u1.toString(), { credentials: "omit" });
        if (!r1.ok) throw new Error(String(r1.status));
        const dto1 = (await r1.json()) as StatusDTO;

        // 2) доп. поля из /api/access/status (trial_ever, trial_* , subscription_until)
        let dto: StatusDTO = dto1;
        try {
          const u2 = new URL(apiUrl("/api/access/status"), window.location.origin);
          if (tg) u2.searchParams.set("tg_user_id", String(tg));
          const r2 = await fetch(u2.toString(), { credentials: "omit" });
          if (r2.ok) {
            const dto2 = (await r2.json()) as StatusDTO;
            // Мерджим, не затирая уже известные значения
            dto = {
              ...dto2,
              ...dto1,
              // приоритет until из payments (если есть), иначе из access
              until: dto1.until ?? dto2.until ?? null,
              // пробрасываем history-флаги
              trial_ever: dto2.trial_ever ?? dto1.trial_ever,
              trial_started_at: dto2.trial_started_at ?? dto1.trial_started_at ?? null,
              trial_expires_at: dto2.trial_expires_at ?? dto1.trial_expires_at ?? null,
              subscription_until: dto2.subscription_until ?? dto1.subscription_until ?? null,
            };
          }
        } catch {
          // если access/status недоступен — используем только payments/status
          dto = dto1;
        }

        if (!cancelled) setStatus(dto);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || "Не удалось получить статус. Обнови страницу.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [from, nav]);

  const view: View = useMemo(() => {
    if (loading) return "loading";
    const s = status || {};

    if (s.needs_policy) return "needs-policy";

    const untilMs = s.until ? new Date(s.until).getTime() : 0;
    const active = s.status === "active" && untilMs > nowMs();
    const trial  = s.status === "trial"  && untilMs > nowMs();
    const has    = Boolean(s.has_access) || active || trial;

    // Был ли когда-то триал/подписка:
    const hadHistory =
      Boolean(s.trial_ever) ||
      Boolean(s.trial_started_at) ||
      Boolean(s.subscription_until);

    // «Истёк» — если когда-то был доступ (триал/подписка), но сейчас его нет.
    // Если есть дедлайн — считаем истечение по нему; если дедлайна нет, но была история — тоже expired.
    const expiredByDeadline = Boolean(untilMs && untilMs <= nowMs());
    const expired = hadHistory && !has && (expiredByDeadline || !s.until);

    if (has) return "has-access";
    if (expired) return "expired";
    // иначе это «чистый» пользователь после политики: ещё не запускал триал
    return "pre-trial";
  }, [status, loading]);

  useEffect(() => {
    if (view !== "has-access") return;
    if (redirectedRef.current) return;
    redirectedRef.current = true;
    const t = setTimeout(() => nav(from, { replace: true }), 10);
    return () => clearTimeout(t);
  }, [view, from, nav]);

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Доступ" to="/" />

      <div className="px-5 pb-24 pt-4 max-w-[720px] mx-auto w-full">
        {err && (
          <div className="rounded-2xl p-3 mb-4 bg-rose-50 border border-rose-100 text-rose-700">
            {err}
          </div>
        )}

        {(view === "loading" || loading) && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5">
            Загружаем статус…
          </div>
        )}

        {/* 1) Политика не принята → вернуть в бота принять правила */}
        {view === "needs-policy" && !loading && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5 space-y-4">
            <div className="text-lg font-semibold">Прими правила в боте</div>
            <div className="text-black/70">
              Открой бот, нажми <b>«Принимаю»</b>. После первого действия триал включится автоматически.
            </div>

            <button
              onClick={() => openInBot("start")}
              className="w-full h-11 rounded-xl bg-black text-white active:scale-[0.99]"
            >
              Вернуться в бот
            </button>
          </div>
        )}

        {/* 2) Новый пользователь, политика уже принята, но триал ещё не стартовал */}
        {view === "pre-trial" && !loading && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5 space-y-4">
            <div className="text-lg font-semibold">5 дней бесплатно</div>
            <div className="text-black/70">
              Сделай любое действие в боте — пробный период включится автоматически.
            </div>
            <button
              onClick={() => openInBot("talk")}
              className="w-full h-11 rounded-xl bg-black text-white active:scale-[0.99]"
            >
              Открыть бот
            </button>
          </div>
        )}

        {/* 3) Истёк триал или подписка */}
        {view === "expired" && !loading && (
          <div className="space-y-3">
            <div className="rounded-3xl p-5 bg-amber-50 border border-amber-100">
              <div className="text-lg font-semibold">Доступ закончился</div>
              <div className="text-black/70">
                Хочу продолжать помогать, но для этого нужна подписка. Вернись в бота и выбери удобный тариф.
              </div>
            </div>

            <button
              onClick={() => openInBot("pay")}
              className="w-full h-11 rounded-xl bg-black text-white active:scale-[0.99]"
            >
              Оплатить в боте
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
