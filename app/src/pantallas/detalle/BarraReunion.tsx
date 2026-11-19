// La forma de la reunión de un vistazo: quién habló y cuándo (tonos de tinta, sin colores), las marcas y las
// coincidencias de la búsqueda. Al pasar el mouse dice quién habla y en qué minuto; un clic lleva el audio ahí.

import { memo, useMemo, useState } from "react";
import type { Marca, Segmento } from "../../api/tipos";
import { mmss } from "../../util/formato";
import type { Coincidencia } from "./buscar";
import { nombreMarca } from "../../componentes/Marcas";

const TONOS = [75, 35, 55, 22, 45]; // % de tinta: «Vos» el más oscuro, «Ellos» claro, el resto alterna
const clave = (s: Segmento) => (s.voz ? `${s.canal}:${s.voz}` : s.canal); // cada voz de «Ellos» con su tono

/** Las franjas no dependen del tiempo ni del puntero: memoizadas, no se redibujan 4 veces por segundo. */
const Franjas = memo(function Franjas({ segmentos, dur, tonos }: { segmentos: Segmento[]; dur: number; tonos: Map<string, number> }) {
  return (
    <div className="absolute inset-x-0 top-[5px] h-[6px] overflow-hidden rounded-full bg-linea">
      {segmentos.map((s, i) =>
        s.inicio == null ? null : (
          <span
            key={s.id ?? i}
            className="absolute top-0 h-full"
            style={{
              left: `${Math.min(100, Math.max(0, (s.inicio / dur) * 100))}%`,
              width: `max(1px, ${(((s.fin ?? s.inicio + 2) - s.inicio) / dur) * 100}%)`,
              background: `color-mix(in srgb, var(--tinta) ${tonos.get(clave(s))}%, transparent)`,
            }}
          />
        ),
      )}
    </div>
  );
});

export function BarraReunion({
  segmentos,
  marcas,
  coincidencias,
  dur,
  t,
  nombre,
  ir,
}: {
  segmentos: Segmento[];
  marcas: Marca[];
  coincidencias: Coincidencia[];
  dur: number;
  t: number;
  nombre: (s: Segmento) => string;
  ir: (s: number) => void;
}) {
  const [encima, setEncima] = useState<{ x: number; texto: string } | null>(null);
  const tonos = useMemo(() => {
    const canales = Array.from(new Set(segmentos.map(clave))).sort((a, b) => (a === "mic" ? -1 : b === "mic" ? 1 : a.localeCompare(b)));
    return new Map(canales.map((c, i) => [c, TONOS[i % TONOS.length]]));
  }, [segmentos]);
  if (!dur) return null;
  const pct = (s: number) => `${Math.min(100, Math.max(0, (s / dur) * 100))}%`;
  // Bisección sobre los inicios (vienen ordenados): el puntero se mueve muchas veces por segundo.
  const quienEn = (seg: number) => {
    let lo = 0;
    let hi = segmentos.length - 1;
    let k = -1;
    while (lo <= hi) {
      const m = (lo + hi) >> 1;
      const x = segmentos[m].inicio;
      if (x == null || x > seg) hi = m - 1;
      else (k = m), (lo = m + 1);
    }
    const s = segmentos[k];
    return s && s.inicio != null && (s.fin ?? s.inicio) + 1 >= seg ? s : undefined;
  };

  return (
    <div
      aria-hidden // atajo visual: con teclado están los tiempos de cada turno, J/L y la búsqueda
      className="relative h-4 cursor-pointer select-none"
      onMouseMove={(e) => {
        const caja = e.currentTarget.getBoundingClientRect();
        const seg = ((e.clientX - caja.left) / caja.width) * dur;
        const s = quienEn(seg);
        setEncima({ x: e.clientX - caja.left, texto: `${s ? nombre(s) : "Silencio"} · ${mmss(seg)}` });
      }}
      onMouseLeave={() => setEncima(null)}
      onClick={(e) => {
        const caja = e.currentTarget.getBoundingClientRect();
        ir(((e.clientX - caja.left) / caja.width) * dur);
      }}
    >
      <Franjas segmentos={segmentos} dur={dur} tonos={tonos} />
      {coincidencias.map(([i], k) => {
        const inicio = segmentos[i]?.inicio;
        return inicio == null ? null : <span key={`c${k}`} className="absolute bottom-0 h-[3px] w-[3px] -translate-x-1/2 rounded-full bg-tinta" style={{ left: pct(inicio) }} />;
      })}
      {marcas.map((m) => (
        <span key={m.id} title={nombreMarca(m)} className="absolute top-0 h-[16px] w-[2px] -translate-x-1/2 rounded-full bg-acento" style={{ left: pct(m.t) }} />
      ))}
      <span className="absolute top-[2px] h-[12px] w-[2px] -translate-x-1/2 rounded-full bg-tinta" style={{ left: pct(t) }} />
      {encima && (
        <span className="pointer-events-none absolute -top-7 -translate-x-1/2 whitespace-nowrap rounded-md bg-tinta px-2 py-1 font-mono text-[11px] text-fondo" style={{ left: encima.x }}>
          {encima.texto}
        </span>
      )}
    </div>
  );
}
