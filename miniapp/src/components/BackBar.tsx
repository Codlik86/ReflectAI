// src/components/BackBar.tsx
import { Link } from "react-router-dom";

type Props = {
  title: string;
  to?: string;                // необязательно
  onBack?: () => void;        // если передан — используем его вместо Link
  right?: React.ReactNode;    // опционально что-то справа
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
        <span className="text-[20px] leading-none">←</span>
      </button>
    ) : to ? (
      <Link
        to={to}
        className="h-10 w-10 rounded-full flex items-center justify-center select-none"
        aria-label="Назад"
      >
        <span className="text-[20px] leading-none">←</span>
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

        {/* Заголовок — прижать к левому краю, как в макете, с 20px отступом */}
        <div className="text-[18px] font-medium text-ink-900">
          {title}
        </div>

        <div className="ml-auto pr-4">{right}</div>
      </div>
    </div>
  );
}
