// src/components/BottomBar.tsx
import { useLocation, useNavigate } from "react-router-dom";
import { HomeIcon, InfoIcon, SettingsIcon } from "./icons";

const ACCENT = "text-[#FF915E]"; // наш акцентный оранжевый

function Item({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      className={[
        // без серого круга: прозрачный фон, только изменение цвета
        "h-12 w-12 flex items-center justify-center rounded-full transition-colors",
        "focus:outline-none focus:ring-0",
      ].join(" ")}
    >
      <div
        className={[
          "transition-colors",
          active ? ACCENT : "text-black/70 hover:text-black",
        ].join(" ")}
      >
        {children}
      </div>
    </button>
  );
}

export default function BottomBar() {
  const nav = useNavigate();
  const { pathname } = useLocation();

  const go = (p: string) => () => nav(p);

  // Главная считается активной и на списках/страницах упражнений/медитаций
  const isHome = pathname === "/";
  const isAbout = pathname.startsWith("/about");
  const isSettings = pathname.startsWith("/settings");

  return (
    <nav
      className="fixed inset-x-0 z-50 pointer-events-none"
      style={{ bottom: "calc(env(safe-area-inset-bottom) + 20px)" }}
      aria-label="Нижняя навигация"
    >
      <div
        className="
          mx-auto pointer-events-auto
          w-[calc(65%-40px)] max-w-[560px] h-14
          rounded-full bg-white
          flex items-center justify-around
          px-2
          shadow-[0_0px_24px_rgba(0,0,0,0.05)]
        "
      >
        <Item label="Главная" active={isHome} onClick={go("/")}>
          <HomeIcon size={24} />
        </Item>

        <Item label="О проекте" active={isAbout} onClick={go("/about")}>
          <InfoIcon size={24} />
        </Item>

        <Item label="Настройки" active={isSettings} onClick={go("/settings")}>
          <SettingsIcon size={24} />
        </Item>
      </div>
    </nav>
  );
}
