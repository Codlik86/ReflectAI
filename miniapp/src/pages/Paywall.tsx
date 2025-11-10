// src/pages/Paywall.tsx
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";
import { fetchPayStatus, type PayStatus } from "../lib/payments";

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

// Варианты экранов
type View =
  | "loading"
  | "has-access"                  // активная подписка ИЛИ активный триал → сразу впускаем в приложение
  | "pre-trial"                   // ещё нет триала и подписки
  | "expired";                    // закончился триал/подписка

const nowMs = () => Date.now();

export default function Paywall() {
  const nav = useNavigate();
  const [status, setStatus] = useState<PayStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // грузим единый статус из /api/payments/status (он знает и про trial, и про подписку)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setErr(null);
      try {
        const s = await fetchPayStatus();
        if (!cancelled) setStatus(s);
      } catch {
        if (!cancelled) setErr("Не удалось получить статус. Обнови страницу.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // маппим ответ на экран
  const view: View = useMemo(() => {
    if (!status) return "loading";

    const trialActive =
      !!status.trial_started &&
      !!status.trial_until &&
      new Date(status.trial_until).getTime() > nowMs();

    const hasSub = !!status.active;

    if (trialActive || hasSub) return "has-access";
    if (status.trial_started && !trialActive && !hasSub) return "expired";

    // сюда попадают «совсем новые»
    return "pre-trial";
  }, [status]);

  // если есть доступ — переносим на главную
  useEffect(() => {
    if (view === "has-access") nav("/", { replace: true });
  }, [view, nav]);

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

        {/* 1) Ещё не запускался триал/нет подписки */}
        {view === "pre-trial" && !loading && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5 space-y-4">
            <div className="text-lg font-semibold">5 дней бесплатно</div>
            <div className="text-black/70">
              Прими правила в боте и сделай любое действие — пробный период включится автоматически.
              Автопродление можно отключить в <b>/pay</b>.
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <button
                onClick={() => openInBot()} // откроет онбординг; там «Принимаю ✅»
                className="h-11 rounded-xl bg-black text-white active:scale-[0.99]"
              >
                Принять правила
              </button>
              <button
                onClick={() => openInBot("WHAT_NEXT")} // вернёт на шаг с «Открыть меню»
                className="h-11 rounded-xl bg-white border border-black/10 active:scale-[0.99]"
              >
                Запустить пробную версию
              </button>
            </div>

            <button
              onClick={() => openInBot("pay")}
              className="w-full h-11 rounded-xl bg-white border border-black/10 active:scale-[0.99]"
            >
              Оплатить подписку (в боте)
            </button>
          </div>
        )}

        {/* 2) Истёк триал/подписка */}
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
