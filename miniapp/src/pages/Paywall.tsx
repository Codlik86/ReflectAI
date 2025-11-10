// src/pages/Paywall.tsx
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";
import { computeAccess } from "../lib/access";

function openBot() {
  const bot = (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";
  const url = `https://t.me/${bot}`;
  // @ts-ignore
  const wa = (window as any)?.Telegram?.WebApp;
  try { if (wa?.openTelegramLink) wa.openTelegramLink(url); else window.location.href = url; }
  catch { window.location.href = url; }
}

type View = "loading" | "has-access" | "pre-trial" | "expired";

export default function Paywall() {
  const nav = useNavigate();
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [access, setAccess] = useState<Awaited<ReturnType<typeof computeAccess>> | null>(null);

  const load = async () => {
    setLoading(true); setErr(null);
    try { setAccess(await computeAccess()); }
    catch { setErr("Не удалось получить статус. Обнови страницу."); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const view: View = useMemo(() => {
    if (!access) return "loading";
    if (access.hasAccess) return "has-access";
    // нет доступа → если триал когда-то запускали, но он уже не активен — expired
    if (access.trialStartedAt && !(access.reason === "trial")) return "expired";
    return "pre-trial";
  }, [access]);

  useEffect(() => { if (view === "has-access") nav("/", { replace: true }); }, [view, nav]);

  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Доступ" to="/" />
      <div className="px-5 pb-24 pt-4 max-w-[720px] mx-auto w-full">
        {err && <div className="rounded-2xl p-3 mb-4 bg-rose-50 border border-rose-100 text-rose-700">{err}</div>}
        {(view === "loading" || loading) && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5">Загружаем статус…</div>
        )}

        {/* До старта триала */}
        {view === "pre-trial" && !loading && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5 space-y-4">
            <div className="text-lg font-semibold">5 дней бесплатно</div>
            <div className="text-black/70">
              Вернись в бот, нажми «Принимаю» на экране правил и сделай любое действие —
              пробный период включится автоматически. Автопродление можно отключить в <b>/pay</b>.
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <button onClick={openBot} className="h-11 rounded-xl bg-black text-white active:scale-[0.99]">
                Принять правила
              </button>
              <button onClick={openBot} className="h-11 rounded-xl bg-white border border-black/10 active:scale-[0.99]">
                Запустить пробную версию
              </button>
            </div>
            <button onClick={openBot} className="w-full h-11 rounded-xl bg-white border border-black/10 active:scale-[0.99]">
              Оплатить подписку (в боте)
            </button>
          </div>
        )}

        {/* Истёк триал / подписка */}
        {view === "expired" && !loading && (
          <div className="space-y-3">
            <div className="rounded-3xl p-5 bg-amber-50 border border-amber-100">
              <div className="text-lg font-semibold">Доступ закончился</div>
              <div className="text-black/70">Чтобы продолжить, оформи подписку — оплата проходит в боте в разделе <b>/pay</b>.</div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <PriceCard title="Неделя"   price="499 ₽" />
              <PriceCard title="Месяц"    price="1 190 ₽" note="Чаще выбирают" />
              <PriceCard title="3 месяца" price="2 990 ₽" />
              <PriceCard title="Год"      price="7 990 ₽" />
            </div>
            <button onClick={openBot} className="w-full h-11 rounded-xl bg-black text-white active:scale-[0.99]">
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
