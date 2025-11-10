// src/pages/Paywall.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import BackBar from "../components/BackBar";
import { ensureAccess } from "../lib/guard";
import { getTelegramUserId } from "../lib/telegram";

/** Открыть чат бота с опциональным start-параметром */
function openInBot(startParam?: string) {
  const bot = (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";
  const url = `https://t.me/${bot}${startParam ? `?start=${encodeURIComponent(startParam)}` : ""}`;
  const wa = (window as any)?.Telegram?.WebApp;
  try {
    if (wa?.openTelegramLink) wa.openTelegramLink(url);
    else window.location.href = url;
  } catch {
    window.location.href = url;
  }
}

type View = "loading" | "has-access" | "pre-trial" | "expired";

type StatusDTO = {
  has_access?: boolean;
  status?: "active" | "trial" | "none" | null;
  until?: string | null;
  plan?: "week" | "month" | "quarter" | "year" | null;
  is_auto_renew?: boolean | null;
};

const nowMs = () => Date.now();

// ---- API base (same as guard.ts) -------------------------------------------
function normalizeBase(s?: string | null) {
  if (!s) return "";
  return s.replace(/\/+$/, "");
}
const API_BASE =
  normalizeBase((import.meta as any)?.env?.VITE_API_BASE) ||
  normalizeBase(window.localStorage.getItem("VITE_API_BASE")) ||
  "";

const apiUrl = (path: string) => (API_BASE ? `${API_BASE}${path}` : path);

// ----------------------------------------------------------------------------

export default function Paywall() {
  const nav = useNavigate();
  const loc = useLocation();
  const redirectedRef = useRef(false);

  // from=/exercises или /meditations — куда вернуть после подтверждения доступа
  const params = new URLSearchParams(loc.search);
  const from = params.get("from") || "/";

  const [status, setStatus] = useState<StatusDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // 1) Быстрый снимок — если уже есть доступ, тихо уходим «домой»
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const snap = await ensureAccess({ startTrial: false });
        if (!cancelled && snap.has_access) {
          if (!redirectedRef.current) {
            redirectedRef.current = true;
            // маленькая задержка, чтобы не мигало
            setTimeout(() => nav(from, { replace: true }), 10);
          }
          return; // не грузим статус — уже редиректим
        }
      } catch {
        // игнор, ниже всё равно попробуем подтянуть статус
      }

      // 2) Тянем статус для выбора экрана paywall
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
    return () => {
      cancelled = true;
    };
  }, [from, nav]);

  // 3) Маппинг статуса на вью
  const view: View = useMemo(() => {
    if (loading) return "loading";
    const s = status || {};
    const untilMs = s.until ? new Date(s.until).getTime() : 0;

    const hasByFlag = !!s.has_access;
    const active = s.status === "active" && untilMs > nowMs();
    const trial = s.status === "trial" && untilMs > nowMs();

    if (hasByFlag || active || trial) return "has-access";

    // если until прошёл — считаем «expired», иначе «pre-trial»
    if (s.status && s.status !== "none" && untilMs && untilMs <= nowMs()) return "expired";
    if (s.status === "none") return "pre-trial";

    // по умолчанию: до запуска триала
    return "pre-trial";
  }, [status, loading]);

  // 4) Если во время показа страницы доступ внезапно появился — мягкий авто-редирект «обратно»
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

        {/* 1) Новый пользователь: policy ещё не принят ИЛИ принят, но триал не запускался */}
        {view === "pre-trial" && !loading && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5 space-y-4">
            <div className="text-lg font-semibold">5 дней бесплатно</div>
            <div className="text-black/70">
              Открой бота, нажми <b>«Принимаю»</b> и сделай любое действие — пробный период включится автоматически.
              Автопродление можно отключить в <b>/pay</b>.
            </div>

            <button
              onClick={() => openInBot()} // онбординг → «Принимаю ✅»
              className="w-full h-11 rounded-xl bg-black text-white active:scale-[0.99]"
            >
              Принять правила в боте
            </button>

            <div className="text-sm text-black/60">
              После принятия правил вернись в мини-апп — доступ откроется автоматически.
            </div>
          </div>
        )}

        {/* 2) Истёк триал или подписка */}
        {view === "expired" && !loading && (
          <div className="space-y-3">
            <div className="rounded-3xl p-5 bg-amber-50 border border-amber-100">
              <div className="text-lg font-semibold">Доступ закончился</div>
              <div className="text-black/70">
                Чтобы продолжить, оформи подписку — оплата проходит в боте в разделе <b>/pay</b>.
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <PriceCard title="Неделя"   price="499 ₽" />
              <PriceCard title="Месяц"    price="1 190 ₽" note="Чаще выбирают" />
              <PriceCard title="3 месяца" price="2 990 ₽" />
              <PriceCard title="Год"      price="7 990 ₽" />
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

function PriceCard({ title, price, note }: { title: string; price: string; note?: string }) {
  return (
    <div className="rounded-2xl border border-black/10 p-4 bg-white/90">
      <div className="font-medium">{title}</div>
      <div className="text-2xl font-semibold my-1">{price}</div>
      {note && <div className="text-sm text-black/60">{note}</div>}
    </div>
  );
}
