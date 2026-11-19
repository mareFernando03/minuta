// Quién habla dentro de «Ellos»: una ficha por cada voz que encontró la diarización. Se le pone nombre en un clic,
// y Minuta sugiere «¿Es Pedro?» cuando la voz se parece a alguien ya nombrado. Lo que se confirma acá mejora la
// huella de esa persona y las sugerencias en las demás reuniones.

import { Check, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { api } from "../../api/cliente";
import type { Reunion, Segmento, Voz } from "../../api/tipos";
import { useAvisos } from "../../componentes/Avisos";

export const vozLegible = (etiqueta: string) => etiqueta.replace(/^V/, "Voz ");

/** Nombre de quien habla en un turno: la persona si se sabe; «Ellos · Voz 2» si hay varias voces sin nombre. */
export function nombreDeTurno(r: Reunion, nombreCanal: (c: string) => string) {
  const voces = new Map((r.voces ?? []).map((v) => [v.etiqueta, v]));
  const varias = voces.size > 1;
  return (s: Segmento) => {
    const v = s.voz ? voces.get(s.voz) : undefined;
    if (v?.nombre) return v.nombre;
    if (s.voz && varias) return `${nombreCanal(s.canal)} · ${vozLegible(s.voz)}`;
    return nombreCanal(s.canal);
  };
}

function minutos(segundos: number | null) {
  if (!segundos) return "";
  return segundos < 60 ? " · menos de 1 min" : ` · ${Math.round(segundos / 60)} min`;
}

export function Voces({ r, alCambiar }: { r: Reunion; alCambiar: () => void }) {
  const [editando, setEditando] = useState<string | null>(null);
  const [personas, setPersonas] = useState<string[]>([]);
  const { avisar } = useAvisos();
  const lista = useId();
  const ruta = (v: Voz) => `/api/reuniones/${encodeURIComponent(r.id)}/voces/${v.etiqueta}`;

  useEffect(() => {
    if (editando) api<{ nombre: string }[]>("/api/voces").then((p) => setPersonas(p.map((x) => x.nombre))).catch(() => {});
  }, [editando]);

  const hacer = async (f: () => Promise<unknown>, aviso?: string) => {
    try {
      await f();
      alCambiar();
      if (aviso) avisar(aviso);
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const nombrar = (v: Voz, nombre: string) => {
    setEditando(null);
    if (!nombre.trim() || nombre.trim() === v.nombre) return;
    hacer(() => api(`${ruta(v)}/nombre`, { metodo: "POST", cuerpo: { nombre: nombre.trim() } }), `${vozLegible(v.etiqueta)} es ${nombre.trim()}: Minuta la va a reconocer en otras reuniones`);
  };

  if (!r.voces?.length) return null;
  return (
    <>
      <datalist id={lista}>
        {personas.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>
      {r.voces.map((v) =>
        editando === v.etiqueta ? (
          <input
            key={v.etiqueta}
            autoFocus
            list={lista}
            aria-label={`Quién es ${vozLegible(v.etiqueta)}`}
            defaultValue={v.nombre ?? ""}
            placeholder={`¿Quién es ${vozLegible(v.etiqueta)}?`}
            onBlur={(e) => nombrar(v, e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") nombrar(v, e.currentTarget.value);
              if (e.key === "Escape") setEditando(null);
            }}
            className="w-44 rounded-full border border-borde bg-fondo px-2.5 py-[3px] outline-none"
          />
        ) : (
          <span key={v.etiqueta} className="inline-flex items-center gap-1">
            <button
              onClick={() => setEditando(v.etiqueta)}
              title={v.nombre ? "Cambiar el nombre" : "Ponerle nombre"}
              className={`rounded-full px-2.5 py-[3px] ${v.nombre ? "bg-papel font-medium" : "border border-dashed border-borde hover:bg-papel"}`}
            >
              {v.nombre ?? vozLegible(v.etiqueta)}
              <span className="text-tenue">
                {v.nombre && v.origen === "auto" ? " · la reconoció Minuta" : ""}
                {!v.nombre ? minutos(v.segundos) : ""}
              </span>
            </button>
            {v.nombre && (
              <button
                aria-label={`Quitar el nombre de ${vozLegible(v.etiqueta)}`}
                title="Quitar el nombre"
                onClick={() => hacer(() => api(`${ruta(v)}/nombre`, { metodo: "DELETE" }))}
                className="grid h-6 w-6 place-items-center rounded-full text-tenue hover:bg-papel hover:text-tinta"
              >
                <X size={12} aria-hidden />
              </button>
            )}
            {!v.nombre && v.sugerida && (
              <span className="inline-flex items-center gap-1 rounded-full border border-borde px-2 py-[2px]">
                ¿Es {v.sugerida.nombre}?
                <button
                  aria-label={`Sí, ${vozLegible(v.etiqueta)} es ${v.sugerida.nombre}`}
                  onClick={() => nombrar(v, v.sugerida!.nombre)}
                  className="grid h-5 w-5 place-items-center rounded-full hover:bg-papel"
                >
                  <Check size={12} aria-hidden />
                </button>
                <button
                  aria-label={`No, ${vozLegible(v.etiqueta)} no es ${v.sugerida.nombre}`}
                  onClick={() => hacer(() => api(`${ruta(v)}/rechazar`, { metodo: "POST" }))}
                  className="grid h-5 w-5 place-items-center rounded-full hover:bg-papel"
                >
                  <X size={12} aria-hidden />
                </button>
              </span>
            )}
          </span>
        ),
      )}
    </>
  );
}
