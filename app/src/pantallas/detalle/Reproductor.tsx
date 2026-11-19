// Barra fija del reproductor: ±15 s, play, posición, velocidad y «Saltear silencios».

import { FastForward, Pause, Play, RotateCcw, RotateCw } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { mmss } from "../../util/formato";
import { Ayuda } from "../../componentes/Ayuda";

const VELOCIDADES = [1, 1.5, 2];

export function Reproductor({
  audio,
  t,
  dur,
  sonando,
  vel,
  setVel,
  saltear,
  setSaltear,
  debajo,
}: {
  audio: RefObject<HTMLAudioElement | null>;
  t: number;
  dur: number;
  sonando: boolean;
  vel: number;
  setVel: (v: number) => void;
  saltear: boolean;
  setSaltear: (v: boolean) => void;
  debajo?: ReactNode;
}) {
  return (
    <div className="sticky top-0 z-10 -mx-1 flex flex-col gap-2 rounded-[10px] border border-linea bg-fondo px-3 py-2.5">
      <div className="flex items-center gap-2.5">
        <Ayuda texto="Retroceder 15 s" atajo="J">
          <button aria-label="Retroceder 15 segundos (J)" onClick={() => audio.current && (audio.current.currentTime -= 15)} className="grid h-8 w-8 place-items-center rounded-md hover:bg-papel">
            <RotateCcw size={17} aria-hidden />
          </button>
        </Ayuda>
        <Ayuda texto={sonando ? "Pausar" : "Reproducir"} atajo="Espacio">
          <button
            aria-label={sonando ? "Pausar (Espacio)" : "Reproducir (Espacio)"}
            onClick={() => (sonando ? audio.current?.pause() : audio.current?.play().catch(() => {}))}
            className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-full bg-tinta text-fondo"
          >
            {sonando ? <Pause size={15} fill="currentColor" aria-hidden /> : <Play size={15} fill="currentColor" aria-hidden />}
          </button>
        </Ayuda>
        <Ayuda texto="Adelantar 15 s" atajo="L">
          <button aria-label="Adelantar 15 segundos (L)" onClick={() => audio.current && (audio.current.currentTime += 15)} className="grid h-8 w-8 place-items-center rounded-md hover:bg-papel">
            <RotateCw size={17} aria-hidden />
          </button>
        </Ayuda>
        <label className="flex min-w-16 flex-1 items-center">
          <span className="sr-only">Posición</span>
          <input
            type="range"
            className="barra w-full"
            min={0}
            max={dur || 0}
            step={0.5}
            value={t}
            style={{ ["--avance" as string]: `${dur ? (t / dur) * 100 : 0}%` }}
            onChange={(e) => audio.current && (audio.current.currentTime = Number(e.target.value))}
          />
        </label>
        <span className="font-mono text-xs text-tenue">
          {mmss(t)} / {mmss(dur)}
        </span>
        <Ayuda texto="Saltear los silencios de más de 2 s">
          <button
            aria-pressed={saltear}
            onClick={() => setSaltear(!saltear)}
            className={`grid h-8 w-8 place-items-center rounded-md border ${saltear ? "border-tinta bg-tinta text-fondo" : "border-borde hover:bg-papel"}`}
          >
            <FastForward size={14} aria-hidden />
            <span className="sr-only">Saltear silencios</span>
          </button>
        </Ayuda>
        <Ayuda texto="Velocidad">
          <button
            aria-label={`Velocidad: ${vel} veces`}
            onClick={() => {
              const v = VELOCIDADES[(VELOCIDADES.indexOf(vel) + 1) % VELOCIDADES.length];
              setVel(v);
              if (audio.current) audio.current.playbackRate = v;
            }}
            className="rounded-md border border-borde px-2 py-1 font-mono text-xs"
          >
            {String(vel).replace(".", ",")}×
          </button>
        </Ayuda>
      </div>
      {debajo}
    </div>
  );
}
