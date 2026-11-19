// Ctrl+F dentro de una reunión: «3 de 12», Enter y Shift+Enter para moverse, Esc para cerrar. Las coincidencias se
// resaltan en el texto y se marcan en el borde del scroll, para ver de un vistazo dónde se habló del tema.

import { ChevronDown, ChevronUp, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { Segmento } from "../../api/tipos";
import { coincidencias, indexar, type Coincidencia } from "./buscar";
import { pintar, rango } from "./resaltado";
import { Ayuda } from "../../componentes/Ayuda";

export function Buscador({
  segmentos,
  parrafos,
  lineas,
  cerrar,
  alMoverse,
  alCambiar,
}: {
  segmentos: Segmento[];
  parrafos: RefObject<(HTMLParagraphElement | null)[]>;
  lineas: RefObject<(HTMLDivElement | null)[]>;
  cerrar: () => void;
  alMoverse: () => void;
  alCambiar?: (lista: Coincidencia[]) => void;
}) {
  const [consulta, setConsulta] = useState("");
  const [actual, setActual] = useState(0);
  const campo = useRef<HTMLInputElement>(null);
  const indice = useMemo(() => indexar(segmentos), [segmentos]);
  const lista = useMemo(() => coincidencias(indice, consulta), [indice, consulta]);
  const [marcas, setMarcas] = useState<{ top: number; left: number; alto: number; puntos: number[] } | null>(null);

  useEffect(() => campo.current?.focus(), []);
  useEffect(() => setActual(0), [consulta]);
  useEffect(() => {
    alCambiar?.(lista);
    return () => alCambiar?.([]);
  }, [lista, alCambiar]);

  // Resaltado de todas y de la actual.
  useEffect(() => {
    pintar(
      "coincidencia",
      lista.map(([i, a, b]) => rango(parrafos.current?.[i], a, b)),
    );
    const c = lista[actual];
    pintar("coincidencia-actual", c ? [rango(parrafos.current?.[c[0]], c[1], c[2])] : []);
    if (c) {
      alMoverse();
      // Inmediato: con «smooth», cada tecla arrancaba un desplazamiento que el siguiente interrumpía y la línea
      // encontrada podía quedar fuera de la vista.
      lineas.current?.[c[0]]?.scrollIntoView({ block: "center", behavior: "auto" });
    }
  }, [lista, actual, parrafos, lineas, alMoverse]);
  useEffect(
    () => () => {
      pintar("coincidencia", []);
      pintar("coincidencia-actual", []);
    },
    [],
  );

  // Marcas en el borde del panel que hace scroll, a la altura proporcional de cada coincidencia.
  useLayoutEffect(() => {
    const panel = document.getElementById("panel-detalle");
    if (!panel || !lista.length) return setMarcas(null);
    const calcular = () => {
      const caja = panel.getBoundingClientRect();
      const turnos = Array.from(new Set(lista.map(([i]) => i)));
      const puntos = turnos.map((i) => {
        const l = lineas.current?.[i];
        return l ? (l.getBoundingClientRect().top - caja.top + panel.scrollTop) / panel.scrollHeight : 0;
      });
      setMarcas({ top: caja.top, left: caja.right - 7, alto: caja.height, puntos });
    };
    calcular();
    window.addEventListener("resize", calcular);
    return () => window.removeEventListener("resize", calcular);
  }, [lista, lineas]);

  const mover = (d: 1 | -1) => lista.length && setActual((a) => (a + d + lista.length) % lista.length);

  return (
    <div role="search" className="flex items-center gap-2 border-t border-linea pt-2">
      <input
        ref={campo}
        value={consulta}
        onChange={(e) => setConsulta(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.preventDefault(), mover(e.shiftKey ? -1 : 1));
          if (e.key === "Escape") (e.preventDefault(), cerrar());
        }}
        placeholder="Buscar en esta reunión"
        aria-label="Buscar en esta reunión"
        className="min-w-0 flex-1 rounded-md border border-borde bg-fondo px-2.5 py-1.5 text-[13.5px] outline-none"
      />
      <span className="min-w-[64px] text-right font-mono text-[12px] text-tenue" aria-live="polite">
        {consulta.trim().length < 2 ? "" : lista.length ? `${actual + 1} de ${lista.length}` : "sin resultados"}
      </span>
      <Ayuda texto="Anterior" atajo="Shift+Enter">
        <button aria-label="Anterior (Shift+Enter)" onClick={() => mover(-1)} className="grid h-7 w-7 place-items-center rounded-md hover:bg-papel">
          <ChevronUp size={15} aria-hidden />
        </button>
      </Ayuda>
      <Ayuda texto="Siguiente" atajo="Enter">
        <button aria-label="Siguiente (Enter)" onClick={() => mover(1)} className="grid h-7 w-7 place-items-center rounded-md hover:bg-papel">
          <ChevronDown size={15} aria-hidden />
        </button>
      </Ayuda>
      <Ayuda texto="Cerrar" atajo="Esc">
        <button aria-label="Cerrar la búsqueda (Esc)" onClick={cerrar} className="grid h-7 w-7 place-items-center rounded-md hover:bg-papel">
          <X size={15} aria-hidden />
        </button>
      </Ayuda>
      {marcas && (
        <div aria-hidden className="pointer-events-none fixed z-30 w-[5px]" style={{ top: marcas.top, left: marcas.left, height: marcas.alto }}>
          {marcas.puntos.map((p, k) => (
            <span key={k} className="absolute left-0 h-[3px] w-full rounded-full bg-acento" style={{ top: `${Math.min(99, p * 100)}%` }} />
          ))}
        </div>
      )}
    </div>
  );
}
