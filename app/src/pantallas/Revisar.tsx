import { useEffect, useState } from "react";
import { api, urlAudio, useDatos } from "../api/cliente";
import type { Ajustes, Grabacion, Reunion } from "../api/tipos";
import { useAvisos } from "../componentes/Avisos";
import { Boton, Cargando, ErrorCaja, Vacio } from "../componentes/Basicos";
import { diaCompleto, horaDe } from "../util/formato";

export function Revisar({ alCambiar }: { alCambiar: () => void }) {
  const { datos, error, cargando, recargar } = useDatos<Grabacion[]>("/api/grabaciones?estado=revisar");
  const { datos: ajustes } = useDatos<Ajustes>("/api/ajustes");
  const [ocultas, setOcultas] = useState<Set<string>>(new Set());
  const lista = (datos ?? []).filter((g) => !ocultas.has(g.id));

  return (
    <main className="flex-1 overflow-y-auto px-[clamp(24px,6vw,96px)] py-12">
      <div className="mx-auto flex max-w-[760px] flex-col gap-8">
        <header className="flex flex-col gap-3">
          <h1 className="m-0 font-serif text-[34px] font-medium tracking-[-0.015em]">Para revisar</h1>
          <p className="m-0 max-w-[60ch] text-[14px] leading-relaxed text-tenue">
            Grabaciones que no coincidieron con ningún evento del calendario ni con una ventana de reunión. Pueden ser llamadas personales: se
            transcribieron solo en esta PC y no se exportaron. Asignales un título para archivarlas, o descartalas.
          </p>
        </header>
        {error ? (
          <ErrorCaja mensaje={error} reintentar={recargar} />
        ) : cargando && !datos ? (
          <Cargando />
        ) : !lista.length ? (
          <Vacio titulo="Nada para revisar">Todo lo grabado se identificó y se archivó.</Vacio>
        ) : (
          lista.map((g) => (
            <Pendiente
              key={g.id}
              g={g}
              carpetas={ajustes?.carpetas ?? []}
              alTerminar={() => {
                setOcultas((o) => new Set(o).add(g.id));
                alCambiar();
              }}
              alRestaurar={() => {
                setOcultas((o) => {
                  const n = new Set(o);
                  n.delete(g.id);
                  return n;
                });
              }}
            />
          ))
        )}
      </div>
    </main>
  );
}

function Pendiente({ g, carpetas, alTerminar, alRestaurar }: { g: Grabacion; carpetas: string[]; alTerminar: () => void; alRestaurar: () => void }) {
  const { avisar, conDeshacer } = useAvisos();
  const { datos: r } = useDatos<Reunion>(g.reunion_id ? `/api/reuniones/${g.reunion_id}` : null, [g.reunion_id]);
  const [titulo, setTitulo] = useState("");
  const [carpeta, setCarpeta] = useState("");
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    urlAudio(g.id).then(setSrc);
  }, [g.id]);

  const asignar = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api(`/api/grabaciones/${g.id}/asignar`, { metodo: "POST", cuerpo: { titulo: titulo.trim(), carpeta: carpeta || null } });
      avisar(`Archivada como «${titulo.trim()}»`);
      alTerminar();
    } catch (err) {
      avisar(err instanceof Error ? err.message : String(err), "error");
    }
  };

  const descartar = () => {
    alTerminar();
    conDeshacer("Se descartó la grabación", () => api(`/api/grabaciones/${g.id}`, { metodo: "DELETE" }), alRestaurar);
  };

  return (
    <article className="flex flex-col gap-4 border-t border-linea pt-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="m-0 font-serif text-[22px] font-medium">Grabación de las {horaDe(g.inicio)}</h2>
        <span className="text-[13px] text-tenue">
          {diaCompleto(g.inicio)} · {Math.round((g.duracion_s ?? 0) / 60)} min · {g.apps.join(", ")}
        </span>
      </div>
      {r && (
        <p className="m-0 line-clamp-3 font-serif text-[16px] leading-relaxed text-tenue">
          {r.segmentos
            .slice(0, 6)
            .map((s) => s.texto)
            .join(" ")}
        </p>
      )}
      {src && <audio controls preload="none" src={src} className="h-9 w-full" aria-label="Escuchar la grabación" />}
      <form onSubmit={asignar} className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-60 flex-1 flex-col gap-1.5 text-[12.5px] text-tenue">
          Título
          <input required value={titulo} onChange={(e) => setTitulo(e.target.value)} className="rounded-lg border border-borde bg-fondo px-3 py-2 text-[14px] text-tinta outline-none" />
        </label>
        <label className="flex flex-col gap-1.5 text-[12.5px] text-tenue">
          Carpeta
          <select value={carpeta} onChange={(e) => setCarpeta(e.target.value)} className="rounded-lg border border-borde bg-fondo px-3 py-2 text-[14px] text-tinta">
            <option value="">Sin carpeta</option>
            {carpetas.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <Boton variante="primario" type="submit">
          Archivar
        </Boton>
        <Boton type="button" variante="fantasma" onClick={descartar}>
          Descartar
        </Boton>
      </form>
    </article>
  );
}
