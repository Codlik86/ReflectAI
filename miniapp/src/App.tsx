// src/App.tsx
import { Outlet } from "react-router-dom";
import Header from "./components/Header";
import BottomBar from "./components/BottomBar";
import "./index.css";

import { initTelegram } from "./lib/telegram";
import { useStartRouter } from "./lib/startRouter";
import { useEffect } from "react";

export default function App() {
  // 1) Инициализация WebApp (безопасно для обычного веба)
  useEffect(() => {
    initTelegram();
  }, []);

  // 2) Авто-роутинг по start-параметру
  useStartRouter();

  return (
    <div className="min-h-screen">
      <Header />
      <main className="mt-4">
        <Outlet />
      </main>
      <BottomBar />
    </div>
  );
}
