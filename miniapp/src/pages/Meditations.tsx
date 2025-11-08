import * as React from "react";
import { Link } from "react-router-dom";
import BackBar from "../components/BackBar";

export type Track = {
  id: string;
  title: string;
  subtitle?: string;
  duration: string;
  url: string;
  catId: "sleep" | "anxiety" | "restore";
};

const LIB: { id: Track["catId"]; title: string; items: Track[] }[] = [
  {
    id: "sleep",
    title: "Сон",
    items: [
      {
        id: "sleep-soft-landing",
        title: "Мягкое засыпание",
        subtitle: "Расслабляемся и отпускаем день",
        duration: "≈ 4:00",
        url: "https://storage.yandexcloud.net/reflectai-audio/sleep.soft_sleep.mp3",
        catId: "sleep",
      },
      {
        id: "sleep-4-7-8",
        title: "Дыхание 4-7-8 (сон)",
        subtitle: "Переход в спокойный режим",
        duration: "≈ 3:59",
        url: "https://storage.yandexcloud.net/reflectai-audio/sleep.478_breath.mp3",
        catId: "sleep",
      },
    ],
  },
  {
    id: "anxiety",
    title: "Тревога",
    items: [
      {
        id: "panic-support",
        title: "Кризисная поддержка",
        subtitle: "При панической атаке",
        duration: "≈ 2:43",
        url: "https://storage.yandexcloud.net/reflectai-audio/panic.attack.mp3",
        catId: "anxiety",
      },
      {
        id: "grounding-54321",
        title: "Заземление 5-4-3-2-1",
        subtitle: "Возвращаем внимание в момент «здесь-и-сейчас»",
        duration: "≈ 3:30",
        url: "https://storage.yandexcloud.net/reflectai-audio/breath54321.mp3",
        catId: "anxiety",
      },
      {
        id: "box-breath",
        title: "Квадратное дыхание",
        subtitle: "Выравниваемся и снижаем напряжение",
        duration: "≈ 3:16",
        url: "https://storage.yandexcloud.net/reflectai-audio/breath4444.mp3",
        catId: "anxiety",
      },
    ],
  },
  {
    id: "restore",
    title: "Восстановление",
    items: [
      {
        id: "body-scan",
        title: "Скан тела",
        subtitle: "Мягкое расслабление от головы до стоп",
        duration: "≈ 3:01",
        url: "https://storage.yandexcloud.net/reflectai-audio/recovery.body_scan.mp3",
        catId: "restore",
      },
      {
        id: "micro-pause",
        title: "Микро-пауза",
        subtitle: "Короткая перезагрузка в течение дня",
        duration: "≈ 2:31",
        url: "https://storage.yandexcloud.net/reflectai-audio/recovery.mini_pause.mp3",
        catId: "restore",
      },
    ],
  },
];

export default function Meditations() {
  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Медитации" to="/" />
      <div style={{ height: "clamp(20px, 3.8vh, 32px)" }} />

      <div
        className="px-5 space-y-7"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}
      >
        {LIB.map((cat) => (
          <section key={cat.id} className="space-y-3">
            <h3 className="text-[18px] leading-6 font-semibold text-ink-900 px-1">
              {cat.title}
            </h3>

            {cat.items.map((t) => (
              <Link
                key={t.id}
                to={`/meditations/${t.id}`}
                state={{ title: t.title, src: t.url }}  // ← передаём в плеер
                className="block"
              >
                <article className="card-btn">
                  <div>
                    <div className="card-title text-[20px] leading-7">{t.title}</div>
                    {t.subtitle && (
                      <div className="text-[14px] leading-6 text-ink-500 mt-1">
                        {t.subtitle}
                      </div>
                    )}
                    <div className="mt-1 flex items-center gap-2 text-[14px] leading-5 text-ink-500">
                      <span
                        aria-hidden
                        className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400 relative top-px"
                      />
                      <span>{t.duration}</span>
                    </div>
                  </div>
                </article>
              </Link>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

export { LIB as MEDITATIONS_LIB };
