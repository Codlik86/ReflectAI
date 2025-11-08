// src/pages/Paywall.tsx
import { useEffect, useMemo, useState } from "react";
import BackBar from "../components/BackBar";
import { fetchPayStatus, createPaymentLink, type PayStatus } from "../lib/payments";

export default function Paywall() {
  const [status, setStatus] = useState<PayStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState<"month" | "year" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ——— helpers ———
  const dateFmt = (iso?: string | null) => {
    if (!iso) return null;
    try {
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, {
        day: "2-digit",
        month: "long",
        year: "numeric",
      });
    } catch {
      return iso || null;
    }
  };

  const banner = useMemo(() => {
    if (!status) return null;

    if (status.active) {
      const u = dateFmt(status.until);
      return {
        tone: "ok",
        title: "Подписка активна",
        text: u ? `Доступ открыт до ${u}.` : "Доступ открыт.",
      };
    }

    if (status.trial_started) {
      const u = dateFmt(status.trial_until);
      return {
        tone: "warn",
        title: "Пробный период активирован",
        text: u ? `Бесплатный доступ до ${u}.` : "Бесплатный доступ активен.",
      };
    }

    return {
      tone: "info",
      title: "5 дней бесплатно",
      text:
        "Полный доступ к упражнениям и медитациям. " +
        "Автопродление можно отключить в любой момент в «Настройках».",
    };
  }, [status]);

  // ——— effects ———
  useEffect(() => {
    let canceled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const s = await fetchPayStatus();
        if (!canceled) setStatus(s);
      } catch {
        if (!canceled) setError("Не удалось получить статус. Обнови страницу.");
      } finally {
        if (!canceled) setLoading(false);
      }
    })();
    return () => {
      canceled = true;
    };
  }, []);

  // ——— actions ———
  const onChoose = async (plan: "month" | "year") => {
    setCreating(plan);
    setError(null);
    try {
      const url = await createPaymentLink(plan);
      window.location.href = url; // редирект на YooKassa
    } catch (e: any) {
      setError(e?.message || "Ошибка оплаты. Попробуй ещё раз.");
    } finally {
      setCreating(null);
    }
  };

  // ——— UI ———
  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Доступ к полному контенту" to="/" />

      <div className="px-5 pb-24 pt-4 max-w-[720px] mx-auto w-full">
        {/* Баннер статуса */}
        {banner && (
          <div
            className={[
              "rounded-3xl p-5 mb-4",
              banner.tone === "ok" && "bg-emerald-50 border border-emerald-100",
              banner.tone === "warn" && "bg-amber-50 border border-amber-100",
              banner.tone === "info" && "bg-white/90 border border-black/5",
            ].join(" ")}
          >
            <div className="text-lg font-semibold">{banner.title}</div>
            <div className="text-black/70 mt-1">{banner.text}</div>
          </div>
        )}

        {/* Ошибка */}
        {error && (
          <div className="rounded-2xl p-3 mb-4 bg-rose-50 border border-rose-100 text-rose-700">
            {error}
          </div>
        )}

        {/* Тарифы */}
        <div className="grid gap-3 sm:grid-cols-2">
          <PlanCard
            title="Месяц"
            price="1 190 ₽"
            note={status?.trial_started ? "После триала" : "Сразу после 5 дней бесплатно"}
            loading={creating === "month" || loading}
            onClick={() => onChoose("month")}
          />

          <PlanCard
            title="Год"
            price="7 990 ₽"
            note="Экономия"
            loading={creating === "year" || loading}
            onClick={() => onChoose("year")}
          />
        </div>

        {/* Юридические */}
        <div className="mt-6 text-sm text-black/60">
          Оплата обрабатывается через YooKassa. Нажимая «Выбрать», ты соглашаешься с офертой и политикой конфиденциальности.
        </div>
      </div>
    </div>
  );
}

function PlanCard(props: {
  title: string;
  price: string;
  note?: string;
  loading?: boolean;
  onClick: () => void;
}) {
  const { title, price, note, loading, onClick } = props;
  return (
    <div className="rounded-2xl border border-black/10 p-4 bg-white/90">
      <div className="font-medium">{title}</div>
      <div className="text-2xl font-semibold my-1">{price}</div>
      {note && <div className="text-sm text-black/60 mb-3">{note}</div>}

      <button
        disabled={loading}
        onClick={onClick}
        className={[
          "w-full h-11 rounded-xl text-white transition",
          loading ? "bg-black/40" : "bg-black hover:bg-black/90 active:scale-[0.99]",
        ].join(" ")}
      >
        {loading ? "Подождите…" : "Выбрать"}
      </button>
    </div>
  );
}
