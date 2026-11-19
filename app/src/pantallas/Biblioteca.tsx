import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, useDatos } from "../api/cliente";
import type { Estado, ResultadoBusqueda, ReunionResumen } from "../api/tipos";
import { Cargando, ErrorCaja, Vacio } from "../componentes/Basicos";
import { EnProceso } from "../componentes/EnProceso";
import { fecha, grupoDeDia, horaDe, minutos, mmss, partesResaltadas } from "../util/formato";
import { Detalle } from "./Detalle";
import type { Salto } from "./detalle/Transcripcion";

const FILTROS = ["Seguimiento Northwind", "Contoso Fabrikam", "Globex", "Orbit", "Acme"];
const CORTO: Record<string, string> = { "Seguimiento Northwind": "Northwind", "Contoso Fabrikam": "Contoso" };

export function Biblioteca({
  estado,
  seleccion,
  seleccionar,
  salto,
}: {
  estado: Estado | null;
  seleccion: string | null;
  seleccionar: (id: string | null) => void;
  salto?: Salto | null;
}) {
  const [carpeta, setCarpeta] = useState<string | null>(null);
  const [consulta, setConsulta] = useState("");
  const [resultados, setResultados] = useState<ResultadoBusqueda[] | null>(null);
  const [saltarA, setSaltarA] = useState<Salto | null>(null);
  const [ocultas, setOcultas] = useState<Set<string>>(new Set());
  const buscador = useRef<HTMLInputElement>(null);
  // Un enlace minuta://…?t= pide ir a un minuto de la reunión seleccionada.
  useEffect(() => {
    if (salto) setSaltarA(salto);
  }, [salto]);
  const ruta = `/api/reuniones?limite=300${carpeta ? `&carpeta=${encodeURIComponent(carpeta)}` : ""}`;
  const { datos, error, cargando, recargar } = useDatos<ReunionResumen[]>(ruta, [carpeta]);

  // Cuando una reunión termina de procesarse, la lista se actualiza sola.
  const ultimaListaRef = useRef(estado?.cola.length);
  useEffect(() => {
    if (ultimaListaRef.current !== estado?.cola.length) recargar();
    ultimaListaRef.current = estado?.cola.length;
  }, [estado?.cola.length, recargar]);

  useEffect(() => {
    const q = consulta.trim();
    if (q.length < 2) return setResultados(null);
    const t = window.setTimeout(() => api<ResultadoBusqueda[]>(`/api/buscar?q=${encodeURIComponent(q)}`).then(setResultados).catch(() => setResultados([])), 180);
    return () => window.clearTimeout(t);
  }, [consulta]);

  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "/" && !(e.target as HTMLElement).closest("input, textarea")) {
        e.preventDefault();
        buscador.current?.focus();
      }
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, []);

  const lista = useMemo(() => (datos ?? []).filter((r) => !ocultas.has(r.id)), [datos, ocultas]);
  const grupos = useMemo(() => {
    const g: { titulo: string; items: ReunionResumen[] }[] = [];
    for (const r of lista) {
      const t = grupoDeDia(r.inicio);
      if (g.at(-1)?.titulo !== t) g.push({ titulo: t, items: [] });
      g.at(-1)!.items.push(r);
    }
    return g;
  }, [lista]);

  useEffect(() => {
    if (!seleccion && lista.length) seleccionar(lista[0].id);
  }, [lista, seleccion, seleccionar]);

  const moverSeleccion = (paso: number) => {
    const i = lista.findIndex((r) => r.id === seleccion);
    const sig = lista[Math.min(lista.length - 1, Math.max(0, i + paso))];
    if (sig) {
      seleccionar(sig.id);
      document.getElementById(`reunion-${sig.id}`)?.scrollIntoView({ block: "nearest" });
    }
  };

  return (
    <div className="flex min-h-0 flex-1">
      <section aria-label="Lista de reuniones" className="flex w-[380px] shrink-0 flex-col border-r border-linea">
        <div className="flex flex-col gap-4 px-6 pb-4 pt-7">
          <h1 className="m-0 font-serif text-[30px] font-medium tracking-[-0.015em]">Reuniones</h1>
          <label className="flex items-center gap-2.5 rounded-lg border border-borde bg-fondo px-3 py-[9px] focus-within:outline-2 focus-within:outline-tinta">
            <Search size={15} className="text-tenue" aria-hidden />
            <span className="sr-only">Buscar en lo que se dijo</span>
            <input
              ref={buscador}
              type="search"
              value={consulta}
              onChange={(e) => setConsulta(e.target.value)}
              placeholder="Buscar en lo que se dijo…"
              className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-tenue"
            />
            <kbd className="rounded border border-linea px-1.5 font-mono text-[11px] text-tenue">/</kbd>
          </label>
          {!resultados && (
            <div role="group" aria-label="Filtrar por frente" className="flex flex-wrap gap-1.5">
              {[null, ...FILTROS].map((f) => (
                <button
                  key={f ?? "todas"}
                  aria-pressed={carpeta === f}
                  onClick={() => setCarpeta(f)}
                  className={`rounded-full border px-[11px] py-[5px] text-xs ${carpeta === f ? "border-tinta bg-tinta text-fondo" : "border-borde hover:bg-papel"}`}
                >
                  {f ? (CORTO[f] ?? f) : "Todas"}
                </button>
              ))}
            </div>
          )}
        </div>

        <div
          className="min-h-0 flex-1 overflow-y-auto pb-6"
          tabIndex={0}
          aria-label="Reuniones, usá las flechas para moverte"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" || e.key === "j") (e.preventDefault(), moverSeleccion(1));
            if (e.key === "ArrowUp" || e.key === "k") (e.preventDefault(), moverSeleccion(-1));
          }}
        >
          {!resultados && <EnProceso estado={estado} alCambiar={recargar} />}
          {resultados ? (
            <Resultados resultados={resultados} consulta={consulta} abrir={(id, s) => (seleccionar(id), setSaltarA(s != null ? { t: s, n: Date.now(), reproducir: true } : null))} />
          ) : error ? (
            <ErrorCaja mensaje={error} reintentar={recargar} />
          ) : cargando && !datos ? (
            <Cargando />
          ) : !lista.length ? (
            <Vacio titulo="Todavía no hay reuniones">Minuta graba sola cuando Chrome, Edge o Slack toman el micrófono. Las reuniones aparecen acá al terminar de transcribirse.</Vacio>
          ) : (
            grupos.map((g) => (
              <div key={g.titulo}>
                <h2 className="m-0 px-6 pb-2 pt-[22px] text-[11px] font-medium uppercase tracking-[0.08em] text-tenue">{g.titulo}</h2>
                {g.items.map((r) => {
                  const min = minutos(r.inicio, r.fin);
                  return (
                    <button
                      key={r.id}
                      id={`reunion-${r.id}`}
                      onClick={() => (seleccionar(r.id), setSaltarA(null))}
                      aria-current={seleccion === r.id}
                      className={`mx-3 block w-[calc(100%-24px)] rounded-lg px-3 py-3.5 text-left ${seleccion === r.id ? "bg-seleccion" : "hover:bg-papel"}`}
                    >
                      <span className="block font-serif text-[18px] leading-snug">{r.titulo}</span>
                      <span className="mt-1 block text-[12.5px] text-tenue">
                        {horaDe(r.inicio)}
                        {min ? ` · ${min} min` : ""} · {r.carpeta ? (CORTO[r.carpeta] ?? r.carpeta) : "sin carpeta"}
                        {r.fuente === "granola" && " · de Granola"}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </section>

      <div id="panel-detalle" className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
        {seleccion ? (
          <Detalle
            key={seleccion}
            id={seleccion}
            saltarA={saltarA}
            alBorrar={(id) => {
              setOcultas((o) => new Set(o).add(id));
              seleccionar(null);
            }}
          />
        ) : (
          !cargando && <Vacio titulo="Elegí una reunión" />
        )}
      </div>
    </div>
  );
}

function Resultados({ resultados, consulta, abrir }: { resultados: ResultadoBusqueda[]; consulta: string; abrir: (id: string, segundo: number | null) => void }) {
  if (!resultados.length) return <Vacio titulo={`Nada dicho con «${consulta.trim()}»`}>Probá con otra palabra. La búsqueda no distingue tildes.</Vacio>;
  return (
    <ul className="flex flex-col">
      {resultados.map((r, i) => (
        <li key={i}>
          <button onClick={() => abrir(r.id, r.segundo)} className="mx-3 block w-[calc(100%-24px)] rounded-lg px-3 py-3 text-left hover:bg-papel">
            <span className="block text-[12.5px] text-tenue">
              {r.titulo} · {fecha(r.fecha).toLocaleDateString("es-AR")}
              {r.segundo != null && ` · en ${mmss(r.segundo)}`}
            </span>
            <span className="mt-1 block font-serif text-[16px] leading-snug">
              {partesResaltadas(r.fragmento).map((p, k) => (p.marca ? <mark key={k}>{p.texto}</mark> : <span key={k}>{p.texto}</span>))}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
