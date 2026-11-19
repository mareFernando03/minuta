// Detalle de una reunión: procedencia, hablantes y la transcripción sincronizada (detalle/Transcripcion.tsx).

import { Cloud, Laptop, MoreHorizontal } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, useDatos } from "../api/cliente";
import type { Reunion } from "../api/tipos";
import { useAvisos } from "../componentes/Avisos";
import { Transcripcion, type Salto } from "./detalle/Transcripcion";
import { nombreDeTurno, Voces } from "./detalle/Voces";
import { EdicionesPropuestas, Resumen } from "./detalle/Resumen";
import { Boton, Cargando, ErrorCaja, Rotulo } from "../componentes/Basicos";
import { diaCompleto, EN_LA_NUBE, fechaCorta, horaDe, NOMBRE_MOTOR } from "../util/formato";

export function Detalle({ id, alBorrar, saltarA }: { id: string; alBorrar: (id: string) => void; saltarA?: Salto | null }) {
  const { datos: r, error, cargando, recargar, setDatos } = useDatos<Reunion>(`/api/reuniones/${encodeURIComponent(id)}`, [id]);
  const { avisar, conDeshacer } = useAvisos();
  const [menu, setMenu] = useState(false);
  const [editando, setEditando] = useState<string | null>(null);
  // Los saltos vienen de afuera (búsqueda, enlace minuta://) o de adentro (las citas del resumen).
  const [salto, setSalto] = useState<Salto | null>(saltarA ?? null);
  useEffect(() => setSalto(saltarA ?? null), [saltarA]);
  const ir = useCallback((t: number) => setSalto({ t, n: Date.now(), reproducir: true }), []);

  useEffect(() => {
    const alCambiar = (e: Event) => (e as CustomEvent<string>).detail === id && recargar();
    window.addEventListener("minuta:reunion-cambio", alCambiar);
    return () => window.removeEventListener("minuta:reunion-cambio", alCambiar);
  }, [id, recargar]);

  if (cargando && !r) return <Cargando />;
  if (error) return <ErrorCaja mensaje={error} reintentar={recargar} />;
  if (!r) return null;

  const nombre = (canal: string) => r.hablantes[canal] ?? (canal === "mic" ? "Vos" : canal === "sistema" ? "Ellos" : canal);
  const nombreTurno = nombreDeTurno(r, nombre);
  const conVoces = !!r.voces?.length;
  const canales = Array.from(new Set(r.segmentos.map((s) => s.canal)));

  const renombrar = async (canal: string, nuevo: string) => {
    setEditando(null);
    const hablantes = { ...r.hablantes, [canal]: nuevo.trim() };
    if (!nuevo.trim()) delete hablantes[canal];
    if (JSON.stringify(hablantes) === JSON.stringify(r.hablantes)) return;
    const x = await api<Reunion>(`/api/reuniones/${encodeURIComponent(r.id)}`, { metodo: "PATCH", cuerpo: { hablantes } });
    setDatos({ ...r, hablantes: x.hablantes });
  };

  const copiarMarkdown = async () => {
    const md = await api<string>(`/api/reuniones/${encodeURIComponent(r.id)}/markdown`, { texto: true });
    await navigator.clipboard.writeText(md);
    avisar("Transcripción copiada como Markdown");
  };

  const reprocesar = async (motor: string) => {
    setMenu(false);
    if (!r.grabacion_id) return;
    try {
      await api(`/api/grabaciones/${r.grabacion_id}/reprocesar`, { metodo: "POST", cuerpo: { motor } });
      avisar(`Se va a volver a transcribir con ${NOMBRE_MOTOR[motor] ?? motor}`);
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };

  const borrar = () => {
    setMenu(false);
    alBorrar(r.id);
    conDeshacer(`Se borró «${r.titulo}»`, () => api(`/api/reuniones/${encodeURIComponent(r.id)}`, { metodo: "DELETE" }), recargar);
  };

  return (
    <article aria-label="Detalle de la reunión" className="mx-auto flex max-w-[68ch] flex-col gap-6 px-[clamp(24px,5vw,72px)] pb-24 pt-9">
      <header className="flex flex-col gap-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Rotulo acento>{r.carpeta ?? "Sin carpeta"}</Rotulo>
          <div className="relative flex gap-2">
            <Boton onClick={copiarMarkdown}>Copiar Markdown</Boton>
            <Boton aria-label="Más acciones" aria-expanded={menu} onClick={() => setMenu((m) => !m)} className="w-9 px-0">
              <MoreHorizontal size={16} aria-hidden />
            </Boton>
            {menu && (
              <div role="menu" className="aparecer absolute right-0 top-11 z-20 flex w-60 flex-col rounded-lg border border-linea bg-fondo p-1 shadow-flotante">
                {r.grabacion_id && r.audio ? (
                  <>
                    <span className="px-3 pb-1 pt-2 text-xs text-tenue">Volver a transcribir con</span>
                    {["assemblyai", "groq", "local"].filter((m) => m === "local" || (r.motores ?? []).includes(m)).map((m) => (
                      <button key={m} role="menuitem" onClick={() => reprocesar(m)} className="rounded-md px-3 py-2 text-left hover:bg-papel">
                        {NOMBRE_MOTOR[m]}
                      </button>
                    ))}
                    <hr className="my-1 border-linea" />
                  </>
                ) : null}
                <button role="menuitem" onClick={borrar} className="rounded-md px-3 py-2 text-left text-acento hover:bg-papel">
                  Borrar reunión
                </button>
              </div>
            )}
          </div>
        </div>
        <h1 className="m-0 font-serif text-[40px] font-medium leading-[1.12] tracking-[-0.02em]">{r.titulo}</h1>
        <p className="m-0 text-[13.5px] text-tenue">
          {diaCompleto(r.inicio)}, {horaDe(r.inicio)}
          {r.fin && <> a {horaDe(r.fin)}</>}
        </p>
        <Procedencia r={r} />
        <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
          <span className="text-tenue">Hablan:</span>
          {canales.slice(0, 8).map((c) =>
            c === "sistema" && conVoces ? (
              <Voces key={c} r={r} alCambiar={recargar} />
            ) : editando === c ? (
              <input
                key={c}
                autoFocus
                aria-label={`Nombre de «${nombre(c)}»`}
                defaultValue={r.hablantes[c] ?? ""}
                placeholder={nombre(c)}
                onBlur={(e) => renombrar(c, e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") renombrar(c, e.currentTarget.value);
                  if (e.key === "Escape") setEditando(null);
                }}
                className="w-40 rounded-full border border-borde bg-fondo px-2.5 py-[3px] outline-none"
              />
            ) : (
              <button
                key={c}
                onClick={() => setEditando(c)}
                title="Ponerle nombre"
                className={`rounded-full px-2.5 py-[3px] ${c === "mic" ? "bg-papel font-semibold" : "border border-dashed border-borde hover:bg-papel"}`}
              >
                {nombre(c)}
                {c === "sistema" && !r.hablantes[c] && <span className="text-tenue"> · ponerle nombre</span>}
              </button>
            ),
          )}
        </div>
      </header>

      <EdicionesPropuestas r={r} alCambiar={recargar} />

      <Resumen r={r} ir={ir} alCambiar={recargar} />

      {r.resumen && (
        <details className="rounded-lg border border-linea px-4 py-3">
          <summary className="cursor-pointer text-[13.5px] font-medium">Resumen de Granola</summary>
          <div className="mt-3 whitespace-pre-wrap font-serif text-[16px] leading-relaxed">{r.resumen}</div>
        </details>
      )}

      <Transcripcion r={r} nombre={nombreTurno} saltarA={salto} alCambiar={recargar} />
    </article>
  );
}

function Procedencia({ r }: { r: Reunion }) {
  if (r.fuente === "granola") return <p className="m-0 text-[12.5px] text-tenue">Importada del archivo de Granola</p>;
  const nube = r.motor ? EN_LA_NUBE.has(r.motor) : false;
  return (
    <p className="m-0 flex flex-wrap items-center gap-2 text-[12.5px] text-tenue">
      <Laptop size={14} aria-hidden /> Grabado en esta PC
      <span aria-hidden>→</span>
      {nube ? <Cloud size={14} aria-hidden /> : <Laptop size={14} aria-hidden />}
      transcripto {nube ? `en ${NOMBRE_MOTOR[r.motor!]} (nube)` : `en esta PC (${NOMBRE_MOTOR[r.motor ?? "local"] ?? r.motor})`}
      {r.audio && r.audio_hasta && (
        <>
          <span aria-hidden>·</span> audio guardado hasta el {fechaCorta(r.audio_hasta + "T00:00:00")}
        </>
      )}
      {r.correcciones.length > 0 && (
        <>
          <span aria-hidden>·</span> {r.correcciones.length} {r.correcciones.length === 1 ? "corrección" : "correcciones"} del glosario
        </>
      )}
    </p>
  );
}
