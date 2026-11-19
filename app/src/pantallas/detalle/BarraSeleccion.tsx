// Barrita flotante sobre lo seleccionado en la transcripción: copiar con formato o borrar esos turnos (texto y audio).

import { Copy, Trash2, UserRound } from "lucide-react";
import { useEffect, useState, type RefObject } from "react";

/** Turnos que toca la selección ahora mismo. Se recalcula al hacer clic: una recarga pudo correr los índices. */
function turnosEnSeleccion(parrafos: (HTMLParagraphElement | null)[]) {
  const s = window.getSelection();
  if (!s || s.isCollapsed || !s.rangeCount) return [];
  const r = s.getRangeAt(0);
  return parrafos.flatMap((p, i) => (p && r.intersectsNode(p) ? [i] : []));
}

interface Seleccion {
  turnos: number[];
  x: number;
  y: number;
}

export function BarraSeleccion({
  parrafos,
  copiar,
  borrar,
  voces = [],
  vozNueva = "V1",
  cambiarVoz,
}: {
  parrafos: RefObject<(HTMLParagraphElement | null)[]>;
  copiar: () => void;
  borrar: (turnos: number[]) => void;
  voces?: { etiqueta: string; nombre: string }[];
  vozNueva?: string;
  cambiarVoz?: (turnos: number[], etiqueta: string) => void;
}) {
  const [sel, setSel] = useState<Seleccion | null>(null);
  const [eligiendoVoz, setEligiendoVoz] = useState(false);

  useEffect(() => {
    const revisar = () => {
      const turnos = turnosEnSeleccion(parrafos.current ?? []);
      if (!turnos.length) return setSel(null), setEligiendoVoz(false);
      const caja = window.getSelection()!.getRangeAt(0).getBoundingClientRect();
      setSel({ turnos, x: caja.left + caja.width / 2, y: caja.top });
    };
    document.addEventListener("selectionchange", revisar);
    return () => document.removeEventListener("selectionchange", revisar);
  }, [parrafos]);

  if (!sel) return null;
  const n = sel.turnos.length;
  return (
    <div
      role="toolbar"
      aria-label="Acciones sobre lo seleccionado"
      className="aparecer fixed z-30 flex -translate-x-1/2 -translate-y-full gap-1 rounded-lg bg-tinta p-1 text-[12.5px] text-fondo shadow-flotante"
      style={{ left: sel.x, top: sel.y - 8 }}
      onMouseDown={(e) => e.preventDefault()} // no perder la selección al tocar la barra
    >
      <button onClick={copiar} className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 hover:bg-white/10">
        <Copy size={13} aria-hidden /> Copiar
      </button>
      <button
        onClick={() => {
          borrar(turnosEnSeleccion(parrafos.current ?? []));
          window.getSelection()?.removeAllRanges();
        }}
        className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 hover:bg-white/10"
      >
        <Trash2 size={13} aria-hidden /> Borrar {n === 1 ? "este turno" : `estos ${n} turnos`}
      </button>
      {cambiarVoz && voces.length > 0 && (
        <button aria-expanded={eligiendoVoz} onClick={() => setEligiendoVoz((e) => !e)} className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 hover:bg-white/10">
          <UserRound size={13} aria-hidden /> Es otra voz…
        </button>
      )}
      {eligiendoVoz && cambiarVoz && (
        <div role="menu" aria-label="A qué voz pasan" className="absolute left-0 top-full mt-1 flex min-w-full flex-col rounded-lg bg-tinta p-1 shadow-flotante">
          {[...voces, { etiqueta: vozNueva, nombre: "Una voz nueva" }].map((v) => (
            <button
              key={v.etiqueta}
              role="menuitem"
              onClick={() => {
                cambiarVoz(turnosEnSeleccion(parrafos.current ?? []), v.etiqueta);
                setEligiendoVoz(false);
                window.getSelection()?.removeAllRanges();
              }}
              className="rounded-md px-2.5 py-1.5 text-left hover:bg-white/10"
            >
              {v.nombre}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
