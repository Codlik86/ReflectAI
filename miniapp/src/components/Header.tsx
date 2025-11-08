// src/components/Header.tsx
import React from "react";
// Положи сюда свой файл 24x24 (png/svg/webp) и поправь имя, если нужно
import logoUrl from "../assets/logo pomni.svg";

export default function Header() {
  return (
    <header
      className="mx-5 mt-5 rounded-2xl bg-white px-4 py-3
                 flex items-center justify-between select-none"
    >
      <div className="flex items-center gap-2">
        {/* Логотип. Если файла нет — блок просто схлопнется и останется текст */}
        {logoUrl ? (
          <img
            src={logoUrl}
            alt="Помни"
            width={24}
            height={24}
            className="w-6 h-6 object-contain"
            draggable={false}
          />
        ) : null}
        <div className="text-[16px] font-semibold">ПОМНИ</div>
      </div>

      <a href="#" className="text-[16px]">
        Открыть бот
      </a>
    </header>
  );
}
