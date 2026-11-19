// Un turno de la transcripción. Está memoizado: el audio avanza cuatro veces por segundo y solo deben volver a
// dibujarse los turnos que cambian (el que deja de sonar y el que empieza), no los ~1500 de una reunión larga.
// Las acciones llegan por una ref estable con los manejadores de la última versión del padre.

import { Copy, Link, Pencil, Trash2 } from "lucide-react";
import { memo, useRef, type RefObject } from "react";
import type { Marca, Segmento } from "../../api/tipos";
import { Ayuda } from "../../componentes/Ayuda";
import { IconoMarca, nombreMarca } from "../../componentes/Marcas";
import { mmss } from "../../util/formato";
import { EditorTurno } from "./EditorTurno";

export interface AccionesTurno {
  ir: (s: number) => void;
  clicEnPalabra: (i: number, parrafo: HTMLParagraphElement, x: number, y: number) => void;
  editar: (id: number | null) => void;
  guardar: (s: Segmento, nuevo: string) => void;
  copiarTurno: (s: Segmento) => void;
  copiarEnlace: (s: Segmento) => void;
  borrar: (indices: number[]) => void;
  registrarLinea: (i: number, el: HTMLDivElement | null) => void;
  registrarParrafo: (i: number, el: HTMLParagraphElement | null) => void;
}

export const Turno = memo(function Turno({
  s,
  i,
  nombre,
  conAudio,
  esActivo,
  editando,
  marcas,
  clicable,
  acciones,
}: {
  s: Segmento;
  i: number;
  nombre: string;
  conAudio: boolean;
  esActivo: boolean;
  editando: boolean;
  marcas: Marca[] | undefined;
  clicable: boolean;
  acciones: RefObject<AccionesTurno>;
}) {
  const a = () => acciones.current;
  const esVos = s.canal === "mic";
  const clicPendiente = useRef<number | undefined>(undefined);
  return (
    <div
      ref={(el) => a().registrarLinea(i, el)}
      className={`group grid grid-cols-[76px_minmax(0,1fr)_28px] gap-x-[18px] rounded-r-lg ${esActivo ? "-ml-4 border-l-2 border-acento bg-acento-suave py-3 pl-[14px] pr-3.5" : ""}`}
    >
      <div className="flex flex-col items-start gap-0.5 pt-1">
        <span className={`text-xs tracking-[0.04em] ${esVos ? "font-semibold" : "font-medium text-tenue"}`}>{nombre}</span>
        {s.inicio != null &&
          (conAudio ? (
            <button onClick={() => a().ir(s.inicio!)} aria-label={`Escuchar desde ${mmss(s.inicio)}`} className="font-mono text-[11.5px] text-tenue underline decoration-linea underline-offset-[3px] hover:text-tinta">
              {mmss(s.inicio)}
            </button>
          ) : (
            <span className="font-mono text-[11.5px] text-tenue">{mmss(s.inicio)}</span>
          ))}
        {esActivo && <span className="mt-1 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-acento">Sonando</span>}
        {marcas?.map((m) => (
          <button
            key={m.id}
            onClick={() => conAudio && a().ir(m.t)}
            title={m.nota ?? undefined}
            aria-label={`${nombreMarca(m)} en ${mmss(m.t)}${m.nota ? `: ${m.nota}` : ""}`}
            className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-acento"
          >
            <IconoMarca size={11} /> {nombreMarca(m)}
          </button>
        ))}
      </div>
      {editando ? (
        <EditorTurno texto={s.texto} guardar={(nuevo) => a().guardar(s, nuevo)} cancelar={() => a().editar(null)} />
      ) : (
        <p
          ref={(el) => a().registrarParrafo(i, el)}
          onClick={(e) => {
            // El clic espera un instante: si era un doble clic (editar), no mueve el audio.
            const { clientX, clientY, currentTarget } = e;
            window.clearTimeout(clicPendiente.current);
            clicPendiente.current = window.setTimeout(() => a().clicEnPalabra(i, currentTarget, clientX, clientY), 250);
          }}
          onDoubleClick={() => {
            window.clearTimeout(clicPendiente.current);
            if (s.id != null) a().editar(s.id);
          }}
          title={s.id != null ? "Doble clic para corregir" : undefined}
          className={`m-0 font-serif text-[18.5px] leading-[1.65] ${clicable ? "cursor-pointer" : ""}`}
        >
          {s.texto}
        </p>
      )}
      <div className="flex flex-col gap-1 pt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        {s.id != null && (
          <Ayuda texto="Corregir" atajo="doble clic" alinear="derecha">
            <button aria-label={`Corregir el turno de ${mmss(s.inicio)}`} onClick={() => a().editar(s.id!)} className="grid h-7 w-7 place-items-center rounded-md text-tenue hover:bg-papel hover:text-tinta">
              <Pencil size={14} aria-hidden />
            </button>
          </Ayuda>
        )}
        <Ayuda texto="Copiar con minuto y quién habla" alinear="derecha">
          <button aria-label={`Copiar el turno de ${mmss(s.inicio)}`} onClick={() => a().copiarTurno(s)} className="grid h-7 w-7 place-items-center rounded-md text-tenue hover:bg-papel hover:text-tinta">
            <Copy size={14} aria-hidden />
          </button>
        </Ayuda>
        {s.id != null && (
          <Ayuda texto="Borrar este turno (texto y audio)" alinear="derecha">
            <button aria-label={`Borrar el turno de ${mmss(s.inicio)}`} onClick={() => a().borrar([i])} className="grid h-7 w-7 place-items-center rounded-md text-tenue hover:bg-papel hover:text-acento">
              <Trash2 size={14} aria-hidden />
            </button>
          </Ayuda>
        )}
        {s.inicio != null && (
          <Ayuda texto="Copiar el enlace a este minuto" alinear="derecha">
            <button aria-label={`Copiar el enlace a ${mmss(s.inicio)}`} onClick={() => a().copiarEnlace(s)} className="grid h-7 w-7 place-items-center rounded-md text-tenue hover:bg-papel hover:text-tinta">
              <Link size={14} aria-hidden />
            </button>
          </Ayuda>
        )}
      </div>
    </div>
  );
});
