// src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import Home from "./pages/Home";
import Exercises from "./pages/Exercises";
import Meditations from "./pages/Meditations";

// Упражнения-страницы
import PMR from "./pages/PMR";
import Grounding54321 from "./pages/Grounding54321";
import Breath46 from "./pages/Breath46";
import Breath4444 from "./pages/Breath4444";
import Breath478 from "./pages/Breath478";
import BodyScan from "./pages/BodyScan";
import ThoughtLabeling from "./pages/ThoughtLabeling";
import Breath333 from "./pages/Breath333";

// Новые страницы
import About from "./pages/About";
import Settings from "./pages/Settings";
import Paywall from "./pages/Paywall";
import MeditationPlayer from "./pages/MeditationPlayer";

// 👇 добавлено: безопасная инициализация Telegram WebApp SDK
import { initTelegram } from "./lib/telegram";
initTelegram(); // вызвать ОДИН раз на старте приложения

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Home /> },
      { path: "exercises", element: <Exercises /> },

      // Новые маршруты упражнений
      { path: "exercises/pmr", element: <PMR /> },
      { path: "exercises/grounding", element: <Grounding54321 /> },
      { path: "exercises/breath-46", element: <Breath46 /> },
      { path: "exercises/breath-4444", element: <Breath4444 /> },
      { path: "exercises/breath-478", element: <Breath478 /> },
      { path: "exercises/body-scan", element: <BodyScan /> },
      { path: "exercises/thought-labeling", element: <ThoughtLabeling /> },
      { path: "exercises/breath-333", element: <Breath333 /> },

      { path: "meditations", element: <Meditations /> },
      { path: "meditations/:id", element: <MeditationPlayer /> },

      // новые
      { path: "about", element: <About /> },
      { path: "settings", element: <Settings /> },
      { path: "paywall", element: <Paywall /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
