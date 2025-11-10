// src/pages/Paywall.tsx
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";
import { fetchPayStatus, type PayStatus } from "../lib/payments";
import { acceptPolicy, getAccessStatus } from "../lib/access"; // ← добавили getAccessStatus

/** Открыть чат бота с опциональным start-параметром */
function openInBot(startParam?: string) {
  const bot = (import.meta as any)?.env?.VITE_BOT_USERNAME || "reflectttaibot";
  const url = `https://t.me/${bot}${startParam ? `?start=${encodeURIComponent(startParam)}` : ""}`;
  // @ts-ignore
  const wa = (window as any)?.Telegram?.WebApp;
  if (wa?.openTelegramLink) wa.openTelegramLink(url);
  else window.location.href = url;
}

const nowMs = () => Date.now();

function fmtDate(iso?: string | null) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      day: "2-digit",
      month: "long",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

type View =
  | "loading"
  | "has-access"
  | "pre-trial-policy-not-accepted"
  | "pre-trial-policy-accepted"
  | "trial-expired";

export default function Paywall() {
  const navigate = useNavigate();

  const [pay, setPay] = useState<PayStatus | null>(null);
  const [policyAccepted, setPolicyAccepted] = useState<boolean | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);

  // ---- загрузка статусов (платёж + доступ) ----
  useEffect(() => {
    let canceled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [p, access] = await Promise.all([
          fetchPayStatus(),
          getAccessStatus().catch(() => ({ ok: false } as any)),
        ]);
        if (!canceled) {
          setPay(p);
          // policy_accepted может отсутствовать → считаем false
          setPolicyAccepted(!!(access as any)?.policy_accepted);
        }
      } catch {
        if (!canceled) setError("Не удалось получить статус. Обнови страницу.");
      } finally {
        if (!canceled) setLoading(false);
      }
    })();
    return () => { canceled = true; };
  }, []);

  // ---- вычисляем «представление» ----
  const view: View = useMemo(() => {
    if (!pay || policyAccepted === null) return "loading";

    const activeSub    = !!pay.active;                 // есть оплаченная подписка
    const trialStarted = !!pay.trial_started;          // триал уже запускали
    const trialUntilMs = pay.trial_until ? new Date(pay.trial_until).getTime() : 0;
    const trialActive  = trialStarted && trialUntilMs > nowMs();

    // есть любой доступ → уводим в приложение
    if (activeSub || trialActive) return "has-access";

    // триал запускали, но он истёк
    if (trialStarted && !trialActive && !activeSub) return "trial-expired";

    // совсем новый пользователь: делим по принятию правил
    return policyAccepted ? "pre-trial-policy-accepted" : "pre-trial-policy-not-accepted";
  }, [pay, policyAccepted]);

  // при доступе — молча уводим в приложение
  useEffect(() => {
    if (view === "has-access") navigate("/", { replace: true });
  }, [view, navigate]);

  // перезагрузка
  const reload = async () => {
    try {
      setLoading(true);
      const [p, access] = await Promise.all([
        fetchPayStatus(),
        getAccessStatus().catch(() => ({ ok: false } as any)),
      ]);
      setPay(p);
      setPolicyAccepted(!!(access as any)?.policy_accepted);
    } catch {
      setError("Не удалось обновить статус.");
    } finally {
      setLoading(false);
    }
  };

  // «Принять правила» в мини-аппе
  const onAcceptPolicy = async () => {
    try {
      setAccepting(true);
      await acceptPolicy();  // сервер отметит принятие
      await reload();
    } catch {
      // если API недоступен — откроем бота
      openInBot("ACCEPT_POLICY");
    } finally {
      setAccepting(false);
    }
  };

  // ---- UI ----
  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Доступ" to="/" />

      <div className="px-5 pb-24 pt-4 max-w-[720px] mx-auto w-full">
        {error && (
          <div className="rounded-2xl p-3 mb-4 bg-rose-50 border border-rose-100 text-rose-700">
            {error}
          </div>
        )}

        {(view === "loading" || loading) && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5">
            Загружаем статус…
          </div>
        )}

        {/* --- 1) Совсем новый, правила НЕ приняты --- */}
        {view === "pre-trial-policy-not-accepted" && !loading && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5 space-y-4">
            <div className="text-lg font-semibold">Перед стартом — короткое правило</div>
            <div className="text-black/70">
              Прими правила в один клик и запусти бесплатный пробный период на 5 дней.
              Автопродление можно отключить в <b>/pay</b>.
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <button
                onClick={onAcceptPolicy}
                disabled={accepting}
                className="h-11 rounded-xl bg-black text-white active:scale-[0.99] disabled:opacity-60"
              >
                {accepting ? "Сохраняем…" : "Принять правила"}
              </button>
              <button
                onClick={() => openInBot("WHAT_NEXT")}
                className="h-11 rounded-xl bg-white border border-black/10 active:scale-[0.99]"
              >
                Открыть бота
              </button>
            </div>
          </div>
        )}

        {/* --- 2) Правила приняты, триал НЕ запускали --- */}
        {view === "pre-trial-policy-accepted" && !loading && (
          <div className="rounded-3xl p-5 bg-white/90 border border-black/5 space-y-4">
            <div className="text-lg font-semibold">5 дней бесплатно</div>
            <div className="text-black/70">
              Чтобы начать, сделай любое действие в боте (например, открой меню или запусти практику) — пробный период включится автоматически.
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <button
                onClick={() => openInBot("WHAT_NEXT")}
                className="h-11 rounded-xl bg-black text-white active:scale-[0.99]"
              >
                Запустить пробную версию
              </button>
              <button
                onClick={() => openInBot("pay")}
                className="h-11 rounded-xl bg-white border border-black/10 active:scale-[0.99]"
              >
                Оплатить подписку
              </button>
            </div>
          </div>
        )}

        {/* --- 3) Истёк триал/подписка --- */}
        {view === "trial-expired" && !loading && (
          <div className="space-y-3">
            <div className="rounded-3xl p-5 bg-amber-50 border border-amber-100">
              <div className="text-lg font-semibold">Пробный период закончился</div>
              <div className="text-black/70">
                Чтобы продолжить, оформите подписку. Я рядом и готов помогать дальше.
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

            <div className="text-sm text-black/60">
              Оплата проходит в чате бота в разделе <b>/pay</b>. Автопродление можно отключить там же.
            </div>
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
