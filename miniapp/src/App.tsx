// src/App.tsx
import * as React from "react";
import { Outlet } from "react-router-dom";
import Header from "./components/Header";
import BottomBar from "./components/BottomBar";
import "./index.css";

import { initTelegram } from "./lib/telegram";
import { useStartRouter } from "./lib/startRouter";

export default function App() {
  // 1) Инициализация WebApp (безопасно для обычного веба)
  React.useEffect(() => {
    try {
      initTelegram();
    } catch {
      // мягко игнорируем: в обычном вебе Telegram WebApp может быть недоступен
    }
  }, []);

  // 2) Авто-роутинг по start-параметру (хук сам защищён от повторов)
  useStartRouter();

  return (
    <div className="min-h-screen flex flex-col bg-bg">
      <Header />
      {/* Основной контент. Отступ снизу под BottomBar, чтобы карточки не перекрывались */}
      <main
        className="mt-4 flex-1"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}
      >
        <Outlet />
      </main>
      <BottomBar />
    </div>
  );
}
