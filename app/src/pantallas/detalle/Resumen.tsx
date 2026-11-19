// Resumen de Minuta (Claude, con plantilla por carpeta), sus acciones y las correcciones que Claude propuso por MCP.
// Cada viñeta lleva sus citas al minuto: la mejor defensa contra un resumen inventado es poder escucharlo.

import { Check, RotateCcw, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/cliente";
import type { Accion, Edicion, Reunion } from "../../api/tipos";
import { useAvisos } from "../../componentes/Avisos";
import { Boton, Rotulo } from "../../componentes/Basicos";
import { fechaCorta, horaDe, mmss } from "../../util/formato";

export function Resumen({ r, ir, alCambiar }: { r: Reunion; ir: (t: number) => void; alCambiar: () => void }) {
  const { avisar } = useAvisos();
  const [pidiendo, setPidiendo] = useState(false);
  const [regenerando, setRegenerando] = useState(false);
  const [pedido, setPedido] = useState("");
  const res = r.resumen_minuta;

  // Llegó el resumen nuevo (el detalle se recarga con el evento «resumen»): se apaga el «resumiendo».
  useEffect(() => setPidiendo(false), [res?.generado]);
  // Si falló (o no había qué resumir), el motor avisa con un error: se apaga el «resumiendo» y se dice por qué.
  useEffect(() => {
    const fin = (ev: Event) => {
      const d = (ev as CustomEvent<{ id: string; error?: string }>).detail;
      if (d?.id !== r.id || !d.error) return;
      setPidiendo(false);
      avisar(d.error, "error");
    };
    window.addEventListener("minuta:resumen", fin);
    return () => window.removeEventListener("minuta:resumen", fin);
  }, [r.id, avisar]);

  if (!res && !r.grabacion_id) return null; // importada de Granola: trae su propio resumen

  const resumir = async () => {
    try {
      await api(`/api/reuniones/${encodeURIComponent(r.id)}/resumir`, { metodo: "POST", cuerpo: pedido.trim() ? { pedido: pedido.trim() } : {} });
      setPidiendo(true);
      setRegenerando(false);
      setPedido("");
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };

  return (
    <section aria-label="Resumen" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <Rotulo>Resumen</Rotulo>
        {res && !pidiendo && (
          <button onClick={() => setRegenerando((x) => !x)} aria-expanded={regenerando} className="text-[12.5px] text-tenue underline decoration-borde underline-offset-4 hover:text-tinta">
            Regenerar…
          </button>
        )}
      </div>

      {pidiendo ? (
        <p role="status" className="m-0 flex items-center gap-2 text-[13.5px] text-tenue">
          <Sparkles size={14} aria-hidden /> Resumiendo con Claude. Aparece acá cuando esté, en uno o dos minutos.
        </p>
      ) : !res ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-[13.5px] text-tenue">Todavía no tiene resumen. Se hace con Claude, con las citas al minuto y las acciones que salieron.</p>
          <Boton className="w-fit" onClick={resumir}>
            <Sparkles size={14} aria-hidden /> Resumir con Claude
          </Boton>
        </div>
      ) : null}

      {regenerando && (
        <div className="flex flex-wrap gap-2">
          <input
            autoFocus
            value={pedido}
            onChange={(e) => setPedido(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") resumir();
              if (e.key === "Escape") setRegenerando(false);
            }}
            maxLength={500}
            placeholder="Opcional: «más corto», «detallá los próximos pasos»"
            aria-label="Qué cambiar del resumen"
            className="min-w-[24ch] flex-1 rounded-lg border border-borde bg-fondo px-3 py-2 text-[13.5px] outline-none focus:border-tinta"
          />
          <Boton variante="primario" onClick={resumir}>
            Regenerar
          </Boton>
        </div>
      )}

      {res && (
        <div className={`flex flex-col gap-5 ${pidiendo ? "opacity-60" : ""}`}>
          {res.secciones.map((s, i) => (
            <div key={i} className="flex flex-col gap-2">
              <h3 className="m-0 font-serif text-[19px] font-medium">{s.titulo}</h3>
              <ul className="m-0 flex list-disc flex-col gap-1.5 pl-5 font-serif text-[16.5px] leading-relaxed">
                {s.vinetas.map((v, j) => (
                  <li key={j}>
                    {v.texto}
                    {v.citas.map((c) => (
                      <button
                        key={c}
                        onClick={() => ir(c)}
                        aria-label={`Escuchar desde ${mmss(c)}`}
                        className="ml-1.5 rounded px-1 align-baseline font-mono text-[11.5px] text-tenue underline decoration-linea underline-offset-4 hover:bg-papel hover:text-tinta"
                      >
                        {mmss(c)}
                      </button>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <p className="m-0 text-[12px] text-tenue">
            Hecho con Claude el {fechaCorta(res.generado)} a las {horaDe(res.generado)}
            {res.con_plantilla ? ", con la plantilla de la carpeta" : ""}
            {res.pedido ? `, pidiendo «${res.pedido}»` : ""}. Las citas llevan al minuto que respalda cada punto.
          </p>
        </div>
      )}

      <Acciones acciones={r.acciones ?? []} ir={ir} alCambiar={alCambiar} />
    </section>
  );
}

function Acciones({ acciones, ir, alCambiar }: { acciones: Accion[]; ir: (t: number) => void; alCambiar: () => void }) {
  const { avisar } = useAvisos();
  const visibles = acciones.filter((a) => a.estado !== "descartada");
  if (!visibles.length) return null;
  const cambiar = async (a: Accion, estado: Accion["estado"]) => {
    try {
      await api(`/api/acciones/${a.id}`, { metodo: "PATCH", cuerpo: { estado } });
      alCambiar();
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };
  return (
    <div className="flex flex-col gap-1">
      <h3 className="m-0 font-serif text-[19px] font-medium">Acciones</h3>
      <p className="m-0 text-[12.5px] text-tenue">Las que aceptes las ve Claude Code por MCP, para llevarlas a tu lista de tareas.</p>
      <ul className="m-0 flex list-none flex-col p-0">
        {visibles.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-linea py-2.5">
            <span className={`flex-1 text-[14.5px] ${a.estado === "aceptada" ? "" : "text-tinta/80"}`}>
              {a.estado === "aceptada" && <Check size={14} aria-label="Aceptada" className="mr-1.5 inline text-tinta" />}
              {a.texto}
              {a.responsable && <span className="text-tenue"> · {a.responsable}</span>}
              {a.t != null && (
                <button onClick={() => ir(a.t!)} aria-label={`Escuchar desde ${mmss(a.t)}`} className="ml-1.5 font-mono text-[11.5px] text-tenue underline decoration-linea underline-offset-4 hover:text-tinta">
                  {mmss(a.t)}
                </button>
              )}
            </span>
            {a.estado === "propuesta" ? (
              <span className="flex gap-1">
                <Boton className="min-h-8 px-2.5 text-[12.5px]" onClick={() => cambiar(a, "aceptada")}>
                  <Check size={13} aria-hidden /> Aceptar
                </Boton>
                <Boton variante="fantasma" className="min-h-8 px-2.5 text-[12.5px]" onClick={() => cambiar(a, "descartada")}>
                  <X size={13} aria-hidden /> Descartar
                </Boton>
              </span>
            ) : (
              <Boton variante="fantasma" className="min-h-8 px-2.5 text-[12.5px]" onClick={() => cambiar(a, "propuesta")}>
                <RotateCcw size={13} aria-hidden /> Deshacer
              </Boton>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Parte un cambio en lo que queda igual al principio y al final, y el tramo que cambia, en palabras enteras: «Contosó»
 * tachado y «Contoso» al lado se lee mejor que «ú» y «ud». */
function tramoCambiado(a: string, b: string) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  while (i > 0 && !/\s/.test(a[i - 1])) i--;
  let j = 0;
  while (j < a.length - i && j < b.length - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++;
  while (j > 0 && !/\s/.test(a[a.length - j])) j--;
  return { antes: a.slice(0, i), quitado: a.slice(i, a.length - j), puesto: b.slice(i, b.length - j), despues: a.slice(a.length - j) };
}

export function EdicionesPropuestas({ r, alCambiar }: { r: Reunion; alCambiar: () => void }) {
  const { avisar } = useAvisos();
  const [abierto, setAbierto] = useState(false);
  const ediciones = r.ediciones ?? [];
  if (!ediciones.length) return null;

  const resolver = async (e: Edicion, aplicar: boolean) => {
    try {
      await api(`/api/ediciones/${e.id}${aplicar ? "/aplicar" : ""}`, { metodo: aplicar ? "POST" : "DELETE" });
      if (aplicar) avisar("Corrección aplicada");
    } catch (err) {
      avisar(err instanceof Error ? err.message : String(err), "error");
    }
    alCambiar();
  };

  return (
    <section aria-label="Correcciones propuestas" className="flex flex-col gap-2 rounded-[10px] border border-linea bg-papel px-4 py-3">
      <button onClick={() => setAbierto((x) => !x)} aria-expanded={abierto} className="flex items-center gap-2 text-left text-[13.5px] font-medium">
        <Sparkles size={14} aria-hidden className="text-acento" />
        Claude propone {ediciones.length === 1 ? "1 corrección" : `${ediciones.length} correcciones`}
        <span className="font-normal text-tenue">· {abierto ? "ocultar" : "ver"}</span>
      </button>
      {abierto && (
        <ul className="m-0 flex list-none flex-col p-0">
          {ediciones.map((e) => {
            const d = tramoCambiado(e.original, e.propuesto);
            return (
              <li key={e.id} className="flex flex-col gap-1.5 border-t border-linea py-2.5">
                <span className="text-[12px] text-tenue">
                  {e.inicio != null && <span className="font-mono">{mmss(e.inicio)} · </span>}
                  {e.motivo ?? "Sin motivo"}
                </span>
                <p className="m-0 font-serif text-[15.5px] leading-relaxed">
                  {d.antes}
                  {d.quitado && <del className="text-acento">{d.quitado}</del>}
                  {d.quitado && d.puesto && " "}
                  {d.puesto && <ins className="bg-fondo no-underline decoration-tinta [text-decoration-line:underline]">{d.puesto}</ins>}
                  {d.despues}
                </p>
                <span className="flex gap-1">
                  <Boton className="min-h-8 px-2.5 text-[12.5px]" onClick={() => resolver(e, true)}>
                    <Check size={13} aria-hidden /> Aplicar
                  </Boton>
                  <Boton variante="fantasma" className="min-h-8 px-2.5 text-[12.5px]" onClick={() => resolver(e, false)}>
                    <X size={13} aria-hidden /> Rechazar
                  </Boton>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
