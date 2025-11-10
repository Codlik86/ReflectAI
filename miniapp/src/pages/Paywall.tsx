// src/pages/Paywall.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";
import { computeAccess } from "../lib/access";

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
const nowMs = () => Date.now();

export default function Paywall() {
  const nav = useNavigate();
  const loc = useLocation();
  const navigatedRef = useRef(false);

  // куда возвращать после получения доступа
  const qs = new URLSearchParams(loc.search);
  const fromPath = qs.get("from");
  const returnTo = fromPath && fromPath.startsWith("/") ? fromPath : "/";

  const [access, setAccess] = useState<Awaited<ReturnType<typeof computeAccess>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // грузим единый снимок доступа
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const s = await computeAccess();
        if (!cancelled) setAccess(s);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || "Не удалось получить статус. Обнови страницу.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // маппим на представление
  const view: View = useMemo(() => {
    if (!access) return "loading";

    // подписка
    const subUntilMs = access.subscriptionUntil ? new Date(access.subscriptionUntil).getTime() : 0;
    const hasSub = !!subUntilMs && subUntilMs > nowMs();

    // триал (по computeAccess — это started + 5дн)
    const trialUntilMs = access.trialUntil ? new Date(access.trialUntil).getTime() : 0;
    const trialActive = !!access.trialStartedAt && trialUntilMs > nowMs();

    if (hasSub || trialActive || access.hasAccess) return "has-access";

    // триал когда-то был, но уже не активен
    if (access.trialStartedAt && !trialActive && !hasSub) return "expired";

    // совсем новый пользователь
    return "pre-trial";
  }, [access]);

  // доступ есть — впускаем (уважаем ?from=)
  useEffect(() => {
    if (view === "has-access" && !navigatedRef.current) {
      navigatedRef.current = true;
      nav(returnTo, { replace: true });
    }
  }, [view, nav, returnTo]);

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
