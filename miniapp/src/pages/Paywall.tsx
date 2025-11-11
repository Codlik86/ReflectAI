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
  needs_policy?: boolean | null; // НОВОЕ
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
        const u = new URL(apiUrl("/api/payments/status"), window.location.origin);
        if (tg) u.searchParams.set("tg_user_id", String(tg));
        const res = await fetch(u.toString(), { credentials: "omit" });
        if (!res.ok) throw new Error(String(res.status));
        const dto = (await res.json()) as StatusDTO;
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
    const hasByFlag = !!s.has_access;
    const active = s.status === "active" && untilMs > nowMs();
    const trial  = s.status === "trial"  && untilMs > nowMs();
    const expired = Boolean(untilMs && untilMs <= nowMs()); // <-- считаем истёкшим по факту дедлайна

    if (hasByFlag || active || trial) return "has-access";
    if (expired) return "expired";                             // <-- не зависим от status !== "none"
    if (s.status === "none") return "pre-trial";
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
