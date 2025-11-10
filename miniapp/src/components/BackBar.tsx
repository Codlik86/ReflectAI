import * as React from "react";
import { Link } from "react-router-dom";

type Props = {
  title: string;
  to?: string;                // если задан — используем Link
  onBack?: () => void;        // если задан — приоритетнее Link
  right?: React.ReactNode;    // опциональный слот справа
};

export default function BackBar({ title, to, onBack, right }: Props) {
  const Left = () =>
    onBack ? (
      <button
        type="button"
        onClick={onBack}
        className="h-10 w-10 rounded-full flex items-center justify-center select-none"
        aria-label="Назад"
      >
        <span aria-hidden className="text-[20px] leading-none">←</span>
      </button>
    ) : to ? (
      <Link
        to={to}
        className="h-10 w-10 rounded-full flex items-center justify-center select-none"
        aria-label="Назад"
      >
        <span aria-hidden className="text-[20px] leading-none">←</span>
      </Link>
    ) : (
      <span className="h-10 w-10" />
    );

  return (
    <div className="px-5">
      <div className="mt-4 mb-3 h-12 w-full bg-white rounded-3xl flex items-center">
        <div className="pl-2 pr-1">
          <Left />
        </div>

        {/* Заголовок — прижат влево, с усечением, чтобы не «прыгала» таблетка */}
        <div
          className="flex-1 min-w-0 text-[18px] font-medium text-ink-900"
          role="heading"
          aria-level={1}
        >
          <span className="block truncate">{title}</span>
        </div>

        <div className="ml-auto pr-4">{right}</div>
      </div>
    </div>
  );
}
