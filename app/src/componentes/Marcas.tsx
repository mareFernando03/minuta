// Momentos marcados durante la reunión (atajo global Ctrl+Alt+Shift+M o botón). Cada marca tiene una etiqueta
// opcional y una nota; se editan acá, en «Ahora» mientras se graba y en el detalle después.

import { useState } from "react";
import { Star, X } from "lucide-react";
import { api } from "../api/cliente";
import type { EtiquetaMarca, Marca } from "../api/tipos";
import { mmss } from "../util/formato";

export const ETIQUETAS: Record<EtiquetaMarca, string> = { accion: "Acción", decision: "Decisión", pregunta: "Pregunta" };
export const ATAJO_MARCA = "Ctrl+Alt+Shift+M";

export function nombreMarca(m: Marca) {
  return m.etiqueta ? ETIQUETAS[m.etiqueta] : m.nota ? "Nota" : "Marca";
}

function FilaMarca({ m, ir, alCambiar }: { m: Marca; ir?: (t: number) => void; alCambiar: () => void }) {
  const [nota, setNota] = useState(m.nota ?? "");
  const guardar = async (cambios: Partial<Marca>) => {
    await api(`/api/marcas/${m.id}`, { metodo: "PATCH", cuerpo: cambios });
    alCambiar();
  };
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-linea py-2.5">
      {ir ? (
        <button onClick={() => ir(m.t)} className="font-mono text-[12px] text-tenue underline decoration-linea underline-offset-4 hover:text-tinta" aria-label={`Escuchar desde ${mmss(m.t)}`}>
          {mmss(m.t)}
        </button>
      ) : (
        <span className="font-mono text-[12px] text-tenue">{mmss(m.t)}</span>
      )}
      <span className="flex gap-1" role="group" aria-label="Etiqueta">
        {(Object.keys(ETIQUETAS) as EtiquetaMarca[]).map((e) => (
          <button
            key={e}
            aria-pressed={m.etiqueta === e}
            onClick={() => guardar({ etiqueta: m.etiqueta === e ? null : e })}
            className={`rounded-full border px-2.5 py-0.5 text-[12px] ${m.etiqueta === e ? "border-tinta bg-tinta text-fondo" : "border-borde text-tenue hover:text-tinta"}`}
          >
            {ETIQUETAS[e]}
          </button>
        ))}
      </span>
      <input
        value={nota}
        onChange={(e) => setNota(e.target.value)}
        onBlur={() => nota !== (m.nota ?? "") && guardar({ nota: nota || null })}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        placeholder="Nota (opcional)"
        aria-label={`Nota de la marca de ${mmss(m.t)}`}
        className="min-w-[12ch] flex-1 rounded-md border border-transparent bg-transparent px-2 py-1 text-[13.5px] outline-none hover:border-linea focus:border-borde"
      />
      <button
        aria-label="Borrar la marca"
        onClick={async () => {
          await api(`/api/marcas/${m.id}`, { metodo: "DELETE" });
          alCambiar();
        }}
        className="grid h-7 w-7 place-items-center rounded-md text-tenue hover:bg-papel hover:text-tinta"
      >
        <X size={14} aria-hidden />
      </button>
    </li>
  );
}

export function ListaMarcas({ marcas, ir, alCambiar }: { marcas: Marca[]; ir?: (t: number) => void; alCambiar: () => void }) {
  if (!marcas.length) return null;
  return (
    <ul className="m-0 flex list-none flex-col p-0">
      {marcas.map((m) => (
        <FilaMarca key={`${m.id}-${m.etiqueta}-${m.nota}`} m={m} ir={ir} alCambiar={alCambiar} />
      ))}
    </ul>
  );
}

export async function marcarAhora(etiqueta?: EtiquetaMarca) {
  return api<Marca>("/api/grabacion-actual/marca", { metodo: "POST", cuerpo: etiqueta ? { etiqueta } : {} });
}

export function IconoMarca({ size = 14 }: { size?: number }) {
  return <Star size={size} aria-hidden className="text-acento" fill="currentColor" />;
}
