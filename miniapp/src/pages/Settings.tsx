// src/pages/Settings.tsx
import BackBar from "../components/BackBar";

export default function Settings() {
  return (
    <div className="min-h-dvh flex flex-col">
      <BackBar title="Настройки" to="/" />
      <div className="px-5 pb-24 pt-4 max-w-[720px] mx-auto w-full">
        <div className="rounded-3xl bg-white/90 p-5">
          <h2 className="text-lg font-semibold mb-3">Личные настройки</h2>

          {/* Заглушки — позже подключим реальные опции */}
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="font-medium">Приватность</div>
                <div className="text-sm text-black/60">
                  Очистка истории и режим без сохранения (скоро).
                </div>
              </div>
              <button className="px-3 py-1.5 rounded-xl bg-black/5 text-sm">
                Открыть
              </button>
            </div>

            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="font-medium">Звуки/вибро</div>
                <div className="text-sm text-black/60">
                  Сигналы для дыхательных практик (скоро).
                </div>
              </div>
              <button className="px-3 py-1.5 rounded-xl bg-black/5 text-sm">
                Открыть
              </button>
            </div>

            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="font-medium">Язык</div>
                <div className="text-sm text-black/60">
                  Русский · другие языки позже.
                </div>
              </div>
              <button className="px-3 py-1.5 rounded-xl bg-black/5 text-sm">
                Открыть
              </button>
            </div>
          </div>

          <div className="mt-6 border-t pt-4 text-sm text-black/60">
            Версия мини-приложения · UI предварительный.
          </div>
        </div>
      </div>
    </div>
  );
}
