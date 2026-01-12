// src/pages/MeditationPlayer.tsx
import * as React from "react";
import { Link, useParams, useLocation, useNavigate } from "react-router-dom";
import BackBar from "../components/BackBar";
import { MEDITATIONS_LIB } from "./Meditations";
import { ensureAccess } from "../lib/guard";
import { trackEvent } from "../lib/events";

type NavState = { title?: string; subtitle?: string; src?: string } | undefined;

function findById(id?: string) {
  if (!id) return undefined;
  for (const cat of MEDITATIONS_LIB) {
    const hit = cat.items.find((t) => t.id === id);
    if (hit) return { ...hit, subtitle: cat.title };
  }
  return undefined;
}

const isIOS =
  typeof navigator !== "undefined" &&
  /iPad|iPhone|iPod/.test(navigator.userAgent) &&
  typeof window !== "undefined" &&
  !("MSStream" in (window as any));

function PlayIcon({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M8 5v14l11-7-11-7z" fill="currentColor" />
    </svg>
  );
}
function PauseIcon({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M6 5h4v14H6zM14 5h4v14h-4z" fill="currentColor" />
    </svg>
  );
}

export default function MeditationPlayer() {
  const { id } = useParams<{ id: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const navState = (location.state as NavState) ?? {};
  const meta = React.useMemo(() => findById(id), [id]);

  const title = navState.title ?? meta?.title ?? "Медитация";
  const subtitle = navState.subtitle ?? meta?.subtitle ?? "";
  const src =
    navState.src ??
    (meta as any)?.url ??
    "https://cdn.pixabay.com/download/audio/2022/03/15/audio_e4a79d.mp3?filename=calm-meditation-110624.mp3";

  // --- гард доступа (БЕЗ автозапуска триала)
  const checkedRef = React.useRef(false);
  React.useEffect(() => {
    if (checkedRef.current) return;
    checkedRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        const snap = await ensureAccess({ startTrial: false });
        if (!cancelled && !snap.has_access) {
          navigate(`/paywall?from=${encodeURIComponent(`/meditations/${id || ""}`)}`, { replace: true });
        }
      } catch {
        if (!cancelled) {
          navigate(`/paywall?from=${encodeURIComponent(`/meditations/${id || ""}`)}`, { replace: true });
        }
      }
    })();
    return () => { cancelled = true; };
  }, [navigate, id]);

  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  const dragging = React.useRef(false);

  const [playing, setPlaying] = React.useState(false);
  const trackedPlayRef = React.useRef(false);
  const [duration, setDuration] = React.useState(0);
  const [current, setCurrent] = React.useState(0);
  const [volume, setVolume] = React.useState(0.9);

  // WebAudio (для iOS громкости)
  const audioCtxRef = React.useRef<AudioContext | null>(null);
  const sourceRef = React.useRef<MediaElementAudioSourceNode | null>(null);
  const gainRef = React.useRef<GainNode | null>(null);

  // --- запрет скролла ТОЛЬКО на этой странице
  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtmlOverflow = html.style.overflow;
    const prevBodyOverflow = body.style.overflow;
    const prevHtmlOverscroll = html.style.overscrollBehavior;

    html.style.overflow = "hidden";
    body.style.overflow = "hidden";
    html.style.overscrollBehavior = "contain";
    return () => {
      html.style.overflow = prevHtmlOverflow;
      body.style.overflow = prevBodyOverflow;
      html.style.overscrollBehavior = prevHtmlOverscroll;
    };
  }, []);

  // --- RAF-тикер
  const rafIdRef = React.useRef<number | null>(null);
  const startTicker = () => {
    stopTicker();
    const loop = () => {
      const a = audioRef.current;
      if (a) setCurrent(a.currentTime || 0);
      rafIdRef.current = requestAnimationFrame(loop);
    };
    rafIdRef.current = requestAnimationFrame(loop);
  };
  const stopTicker = () => {
    if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
    rafIdRef.current = null;
  };

  const ensureWebAudio = async () => {
    if (!isIOS) return;
    const el = audioRef.current;
    if (!el) return;

    if (!audioCtxRef.current) {
      const Ctx: any = (window as any).AudioContext || (window as any).webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      audioCtxRef.current = ctx;
      try {
        const srcNode = ctx.createMediaElementSource(el); // требует CORS
        const gain = ctx.createGain();
        gain.gain.value = volume;
        srcNode.connect(gain).connect(ctx.destination);
        sourceRef.current = srcNode;
        gainRef.current = gain;
      } catch {
        return;
      }
    }
    const ctx = audioCtxRef.current;
    if (ctx && ctx.state === "suspended") {
      try { await ctx.resume(); } catch {}
    }
  };

  // Подписки и смена src
  React.useEffect(() => {
    const a = audioRef.current;
    if (!a) return;

    a.crossOrigin = "anonymous";

    const onLoaded = () => setDuration(Number.isFinite(a.duration) ? a.duration : 0);
    const onTime = () => { if (!dragging.current) setCurrent(a.currentTime || 0); };
    const onPlay = () => {
      setPlaying(true);
      startTicker();
      if (!trackedPlayRef.current) {
        trackedPlayRef.current = true;
        trackEvent("miniapp_action", "meditation_started", { meditation_id: id || title });
      }
    };
    const onPause = () => { setPlaying(false); stopTicker(); };
    const onEnded = () => { setPlaying(false); stopTicker(); setCurrent(a.duration || 0); };
    const onError = () => { setPlaying(false); stopTicker(); };

    a.addEventListener("loadedmetadata", onLoaded);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onEnded);
    a.addEventListener("error", onError);

    setPlaying(false);
    setCurrent(0);
    a.pause();
    a.src = src;
    a.load();

    return () => {
      stopTicker();
      a.pause();
      a.removeEventListener("loadedmetadata", onLoaded);
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onEnded);
      a.removeEventListener("error", onError);
    };
  }, [src]);

  // Громкость
  React.useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    if (isIOS) {
      if (gainRef.current) gainRef.current.gain.value = volume;
      a.volume = 1;
    } else {
      a.volume = volume;
      if (gainRef.current) gainRef.current.gain.value = volume;
    }
  }, [volume]);

  // Чистка WebAudio
  React.useEffect(() => {
    return () => {
      try {
        stopTicker();
        sourceRef.current?.disconnect();
        gainRef.current?.disconnect();
        audioCtxRef.current?.close();
      } catch {}
    };
  }, []);

  const togglePlay = async () => {
    const a = audioRef.current;
    if (!a) return;
    await ensureWebAudio();

    if (playing) { a.pause(); return; }
    try {
      if (a.readyState < 2) a.load();
      await a.play();
    } catch {
      const once = () => { a.removeEventListener("canplay", once); a.play().catch(() => {}); };
      a.addEventListener("canplay", once, { once: true });
    }
  };

  const seekTo = (sec: number) => {
    const a = audioRef.current;
    if (!a || !duration) return;
    const v = Math.max(0, Math.min(duration, sec));
    a.currentTime = v;
    setCurrent(v);
  };
  const skip = (d: number) => seekTo((current || 0) + d);

  const fmt = (s: number) => {
    const mm = Math.floor(s / 60).toString();
    const ss = Math.floor(s % 60).toString().padStart(2, "0");
    return `${mm}:${ss}`;
  };
  const p = duration > 0 ? current / duration : 0;

  return (
    <div className="flex flex-col" style={{ height: "100dvh", overflow: "hidden" }}>
      <BackBar title={title} to="/meditations" />

      <audio ref={audioRef} preload="auto" playsInline />

      <div
        className="px-5 pb-6 flex-1 overflow-hidden"
        style={{ paddingBottom: "calc(env(safe-area-inset-bottom) + 98px)" }}
      >
        <div className="flex justify-center mt-2">
          <div
            className="w-[80%] max-w-[520px] aspect-square rounded-3xl"
            style={{
              background:
                "radial-gradient(45% 45% at 50% 45%, rgba(255,255,255,.92) 0%, rgba(255,231,216,.72) 35%, rgba(255,218,238,.54) 68%, rgba(255,255,255,0) 76%)",
            }}
            aria-label="Обложка медитации"
          />
        </div>

        <div className="mt-6 text-center">
          {!!subtitle && (
            <div className="text-[13px] tracking-wide uppercase text-black/50 mb-1 select-none">{subtitle}</div>
          )}
          <h1 className="text-[20px] leading-7 font-semibold text-black/85">{title}</h1>
        </div>

        <div className="mt-5 rounded-3xl bg-white/70 backdrop-blur px-5 pt-6 pb-6">
          <div
            className="relative h-[6px] w-full rounded-full bg-black/12 select-none"
            style={{ touchAction: "none" }}
            onPointerDown={(e) => {
              if (!duration) return;
              dragging.current = true;
              (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
              const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
              seekTo((duration || 0) * (x / rect.width));
            }}
            onPointerMove={(e) => {
              if (!dragging.current || !duration) return;
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
              const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
              seekTo((duration || 0) * (x / rect.width));
            }}
            onPointerUp={() => (dragging.current = false)}
            onPointerCancel={() => (dragging.current = false)}
            onPointerLeave={() => (dragging.current = false)}
            aria-label="Полоса прогресса"
            role="slider"
            aria-valuemin={0}
            aria-valuemax={duration || 0}
            aria-valuenow={current || 0}
          >
            <div
              className="absolute left-0 top-0 h-full rounded-full"
              style={{
                width: `${(p * 100).toFixed(2)}%`,
                background: "linear-gradient(90deg, rgba(47,47,47,.9), rgba(47,47,47,.9))",
                transition: dragging.current ? "none" : "width 120ms linear",
              }}
            />
            <div
              className="absolute -top-[7px] h-[18px] w-[18px] rounded-full bg-[rgba(47,47,47,.9)]"
              style={{ left: `calc(${(p * 100).toFixed(2)}% - 9px)` }}
            />
          </div>

          <div className="mt-2 flex justify-between text-[14px] text-black/65 tabular-nums">
            <span aria-label="Текущее время">{fmt(current)}</span>
            <span aria-label="Длительность">{fmt(duration)}</span>
          </div>

          <div className="mt-4 flex items-center justify-center gap-10">
            <button
              className="px-2 py-2 text-[22px] text-[rgba(47,47,47,.95)]"
              style={{ touchAction: "manipulation" }}
              onPointerDown={(e) => e.preventDefault()}
              onPointerUp={() => skip(-15)}
              aria-label="Назад 15 секунд"
            >
              {"\u25C0\u25C0"}
            </button>

            <button
              className="px-2 py-2 text-[rgba(47,47,47,.95)]"
              style={{ touchAction: "manipulation" }}
              onPointerDown={(e) => e.preventDefault()}
              onPointerUp={togglePlay}
              aria-label={playing ? "Пауза" : "Пуск"}
            >
              {playing ? <PauseIcon /> : <PlayIcon />}
            </button>

            <button
              className="px-2 py-2 text-[22px] text-[rgba(47,47,47,.95)]"
              style={{ touchAction: "manipulation" }}
              onPointerDown={(e) => e.preventDefault()}
              onPointerUp={() => skip(+15)}
              aria-label="Вперёд 15 секунд"
            >
              {"\u25B6\u25B6"}
            </button>
          </div>

          <div className="mt-2">
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={volume}
              onChange={(e) => setVolume(parseFloat(e.target.value))}
              className="w-full accent-[rgba(47,47,47,.95)]"
              aria-label="Громкость"
            />
          </div>
        </div>

        <div className="mt-4 text-center">
          <Link to="/meditations" className="text-ink-500 text-[14px]">К списку медитаций</Link>
        </div>
      </div>
    </div>
  );
}
