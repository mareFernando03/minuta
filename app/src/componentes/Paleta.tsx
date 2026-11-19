// Ctrl+K, la única capa de teclado: acciones, secciones y buscar en lo que se dijo en las reuniones, sin IA. La vista
// previa a la derecha sigue a las flechas (Spotlight, Raycast).

import { Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api/cliente";
import type { Estado, ResultadoBusqueda } from "../api/tipos";
import { fechaCorta, mmss, partesResaltadas } from "../util/formato";
import type { Seccion } from "./Barra";

interface Item {
  clave: string;
  grupo: string;
  titulo: ReactNode;
  texto: string;
  detalle?: string;
  atajo?: string;
  resultado?: ResultadoBusqueda;
  hacer: () => void;
}

const SECCIONES: [Seccion, string, string][] = [
  ["ahora", "Ahora", "Ctrl 1"],
  ["reuniones", "Reuniones", "Ctrl 2"],
  ["revisar", "Para revisar", "Ctrl 3"],
  ["motores", "Motores", "Ctrl 4"],
  ["glosario", "Glosario", "Ctrl 5"],
  ["ajustes", "Ajustes", "Ctrl ,"],
];

function normal(t: string) {
  return t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function Fragmento({ texto }: { texto: string }) {
  return (
    <>
      {partesResaltadas(texto).map((p, i) => (p.marca ? <mark key={i}>{p.texto}</mark> : <span key={i}>{p.texto}</span>))}
    </>
  );
}

export function Paleta({
  abierta,
  cerrar,
  ir,
  abrirReunion,
  estado,
  alPausar,
  verAtajos,
}: {
  abierta: boolean;
  cerrar: () => void;
  ir: (s: Seccion) => void;
  abrirReunion: (id: string, segundo?: number | null) => void;
  estado: Estado | null;
  alPausar: (pausado: boolean) => void;
  verAtajos: () => void;
}) {
  const [q, setQ] = useState("");
  // La respuesta guarda la consulta a la que contesta: mientras no llega la de lo que está escrito, no hay
  // resultados (si no, un Enter rápido abría el mejor resultado de la búsqueda anterior).
  const [respuesta, setRespuesta] = useState<{ q: string; r: ResultadoBusqueda[] | null; error?: string } | null>(null);
  const resultados = respuesta?.q === q ? respuesta.r : null;
  const fallo = respuesta?.q === q ? respuesta.error : undefined;
  const enterPendiente = useRef<string | null>(null);
  const [sel, setSel] = useState(0);
  const entrada = useRef<HTMLInputElement>(null);
  const lista = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (abierta) (setQ(""), setSel(0), setTimeout(() => entrada.current?.focus(), 0));
  }, [abierta]);

  // La búsqueda va al motor con una pausa corta: se busca mientras se escribe, no en cada tecla.
  useEffect(() => {
    if (!abierta || !q.trim()) return;
    let vigente = true;
    const t = window.setTimeout(() => {
      api<ResultadoBusqueda[]>(`/api/buscar?q=${encodeURIComponent(q)}&prefijo=true`)
        .then((r) => vigente && setRespuesta({ q, r }))
        .catch((e) => vigente && setRespuesta({ q, r: null, error: e instanceof Error ? e.message : String(e) }));
    }, 120);
    return () => ((vigente = false), window.clearTimeout(t));
  }, [q, abierta]);

  const hecho = useCallback((f: () => void) => () => (f(), cerrar()), [cerrar]);

  const items = useMemo<Item[]>(() => {
    const pausado = !!estado?.ahora.pausado;
    const coincide = (t: string) => !q.trim() || normal(t).includes(normal(q.trim()));
    const acciones: Item[] = [
      { clave: "pausar", grupo: "Acciones", titulo: pausado ? "Reanudar las grabaciones" : "Pausar las grabaciones", texto: "pausar reanudar grabaciones", atajo: "Ctrl Shift P", detalle: "Minuta deja de grabar (o vuelve a grabar) las reuniones.", hacer: hecho(() => alPausar(!pausado)) },
      { clave: "atajos", grupo: "Acciones", titulo: "Ver los atajos de teclado", texto: "atajos teclado", atajo: "Ctrl /", detalle: "La lista de atajos, agrupados por dónde funcionan.", hacer: hecho(verAtajos) },
      ...SECCIONES.map(([id, nombre, atajo]) => ({ clave: `ir-${id}`, grupo: "Ir a", titulo: nombre, texto: `ir a ${nombre}`, atajo, detalle: `Abre ${nombre}.`, hacer: hecho(() => ir(id)) })),
    ].filter((i) => coincide(i.texto) || coincide(String(i.titulo)));

    const reuniones: Item[] = (q.trim() ? resultados ?? [] : []).map((r) => ({
      clave: `${r.id}-${r.segundo ?? ""}`, // una reunión aparece una vez por momento que coincide
      grupo: "Reuniones",
      titulo: r.titulo,
      texto: r.titulo,
      resultado: r,
      atajo: r.segundo != null ? mmss(r.segundo) : undefined,
      hacer: hecho(() => abrirReunion(r.id, r.segundo)),
    }));
    // Una acción o sección que coincide con lo escrito va primero («glosario» lleva a Glosario); después, lo que se dijo.
    return [...acciones, ...reuniones];
  }, [q, resultados, estado, hecho, alPausar, verAtajos, ir, abrirReunion]);

  // Un Enter apretado antes de que llegue la respuesta de lo escrito se cumple cuando llega, con el resultado bueno.
  useEffect(() => {
    if (enterPendiente.current !== null && enterPendiente.current === q && (resultados || fallo)) {
      enterPendiente.current = null;
      items[0]?.hacer();
    }
  }, [resultados, fallo, q, items]);

  useEffect(() => setSel((s) => Math.min(s, Math.max(0, items.length - 1))), [items.length]);
  useEffect(() => {
    lista.current?.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  if (!abierta) return null;
  const actual = items[sel];
  const grupos = items.reduce<{ nombre: string; desde: number }[]>((gs, it, i) => (gs.at(-1)?.nombre === it.grupo ? gs : [...gs, { nombre: it.grupo, desde: i }]), []);
  const tecla = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") return (e.preventDefault(), cerrar());
    if (e.key === "Enter") {
      e.preventDefault();
      if (q.trim() && !resultados && !fallo) return void (enterPendiente.current = q); // todavía no llegó la búsqueda
      return actual?.hacer();
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (e.ctrlKey) {
        const g = grupos.findIndex((x, i) => sel >= x.desde && (i === grupos.length - 1 || sel < grupos[i + 1].desde));
        const otro = grupos[Math.max(0, Math.min(grupos.length - 1, g + (e.key === "ArrowDown" ? 1 : -1)))];
        return otro && setSel(otro.desde);
      }
      setSel((s) => (s + (e.key === "ArrowDown" ? 1 : items.length - 1)) % Math.max(1, items.length));
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-tinta/20 px-4 pt-[8vh]" onMouseDown={cerrar}>
      <div
        role="dialog"
        aria-label="Buscar o hacer algo"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={tecla}
        className="aparecer flex max-h-[80vh] w-[min(920px,100%)] flex-col overflow-hidden rounded-xl border border-linea bg-fondo shadow-flotante"
      >
        <div className="flex items-center gap-2.5 border-b border-linea px-4">
          <Search size={16} aria-hidden className="text-tenue" />
          <input
            ref={entrada}
            autoFocus
            value={q}
            onChange={(e) => (setQ(e.target.value), setSel(0))}
            placeholder="Buscar en las reuniones o escribir una acción…"
            aria-label="Buscar o escribir una acción"
            role="combobox"
            aria-expanded="true"
            aria-controls="paleta-resultados"
            aria-activedescendant={actual ? `paleta-${sel}` : undefined}
            className="min-w-0 flex-1 bg-transparent py-3.5 font-serif text-[18px] outline-none placeholder:text-tenue focus-visible:outline-none"
          />
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-2">
          <div ref={lista} id="paleta-resultados" role="listbox" className="min-h-0 overflow-y-auto border-linea p-1.5 md:border-r">
            {fallo && <p className="px-3 py-3 text-[13px] text-acento">No se pudo buscar: {fallo}</p>}
            {items.length === 0 && !fallo && <p className="px-3 py-6 text-center text-[13.5px] text-tenue">Nada con eso. Probá con otra palabra.</p>}
            {grupos.map((g, gi) => (
              <div key={g.nombre + gi}>
                <div className="px-3 pb-1 pt-3 text-[11px] uppercase tracking-[0.08em] text-tenue">{g.nombre}</div>
                {items.slice(g.desde, grupos[gi + 1]?.desde ?? items.length).map((it, k) => {
                  const i = g.desde + k;
                  return (
                    <div
                      key={it.clave}
                      id={`paleta-${i}`}
                      data-i={i}
                      role="option"
                      aria-selected={i === sel}
                      onMouseMove={() => i !== sel && setSel(i)}
                      onClick={it.hacer}
                      className={`flex cursor-pointer items-baseline justify-between gap-3 rounded-md px-3 py-2 ${i === sel ? "bg-seleccion" : ""}`}
                    >
                      <span className="min-w-0 truncate text-[13.5px]">{it.titulo}</span>
                      {it.atajo && <kbd className="shrink-0 font-mono text-[11px] text-tenue">{it.atajo}</kbd>}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          <VistaPrevia item={actual} />
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-linea px-4 py-2 text-[11.5px] text-tenue">
          <span><b className="font-mono font-medium text-tinta">↑ ↓</b> moverse</span>
          <span><b className="font-mono font-medium text-tinta">Enter</b> abrir</span>
          <span><b className="font-mono font-medium text-tinta">Ctrl ↓</b> siguiente grupo</span>
          <span><b className="font-mono font-medium text-tinta">Esc</b> cerrar</span>
        </div>
      </div>
    </div>
  );
}

/** Lo que se ve a la derecha del elegido: el momento de la reunión donde se dijo, o qué hace la acción. */
function VistaPrevia({ item }: { item: Item | undefined }) {
  if (!item) return <div className="hidden md:block" />;
  const r = item.resultado;
  return (
    <div className="hidden min-h-0 flex-col gap-2.5 overflow-y-auto px-5 py-4 md:flex">
      <span className="text-[11.5px] uppercase tracking-[0.08em] text-tenue">
        {r ? "Reunión" : item.grupo}
        {r?.carpeta && ` · ${r.carpeta}`}
        {r?.fecha && ` · ${fechaCorta(r.fecha)}`}
      </span>
      <h3 className="m-0 font-serif text-[20px] font-medium leading-snug">{item.titulo}</h3>
      {!r && item.detalle && <p className="m-0 text-[14px] text-tenue">{item.detalle}</p>}
      {r?.fragmento && (
        <p className="m-0 font-serif text-[15px] leading-relaxed">
          <Fragmento texto={r.fragmento} />
        </p>
      )}
      {r?.segundo != null && <p className="m-0 text-[13px] text-tenue">Enter abre la reunión en {mmss(r.segundo)}.</p>}
    </div>
  );
}
