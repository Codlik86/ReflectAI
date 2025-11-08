// src/components/icons.ts
import * as React from "react";

type IconProps = {
  size?: number;      // px
  stroke?: number;    // strokeWidth
  className?: string;
};

const common = (size: number, stroke: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: stroke,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  vectorEffect: "non-scaling-stroke" as const,
});

export function HomeIcon({ size = 24, stroke = 1.75, className }: IconProps) {
  return (
    <svg {...common(size, stroke)} className={className}>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 10v8.5A2.5 2.5 0 0 0 7.5 21h9A2.5 2.5 0 0 0 19 18.5V10" />
      <path d="M9.5 21V13h5v8" />
    </svg>
  );
}

export function InfoIcon({ size = 24, stroke = 1.75, className }: IconProps) {
  return (
    <svg {...common(size, stroke)} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 10v7" />
      <path d="M12 7.5h.01" />
    </svg>
  );
}

export function SettingsIcon({ size = 24, stroke = 1.75, className }: IconProps) {
  return (
    <svg {...common(size, stroke)} className={className}>
      <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" />
      <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1A2 2 0 0 1 7.1 3.3l.1.1a1.6 1.6 0 0 0 1.8.3 1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8 1.6 1.6 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z" />
    </svg>
  );
}
