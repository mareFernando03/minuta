// Panel de la bandeja (clic izquierdo en el ícono): el estado de un vistazo y las tareas comunes.
// Es una ventana aparte, sin marco, que Rust ubica sobre la barra de tareas y oculta al perder el foco.

import { useCallback, useEffect, useState } from "react";
import { AppWindow, Pause, Play, Scissors, Star } from "lucide-react";
import { ATAJO_MARCA, marcarAhora } from "./componentes/Marcas";
import { api, enTauri, useEventos } from "./api/cliente";
import type { Estado, ReunionResumen } from "./api/tipos";
import { Alertas } from "./componentes/Alertas";
import { Anotar } from "./componentes/Anotar";
import { GrabarAMano } from "./componentes/GrabarAMano";
import { Boton, PuntoGrabando, Rotulo } from "./componentes/Basicos";
import type { Seccion } from "./componentes/Barra";
import { horaDe, mmss } from "./util/formato";

// Fuera de Tauri (Playwright, o Vite en el navegador) el panel se dibuja igual y estas acciones no hacen nada.
async function abrir(seccion: Seccion, reunion?: string) {
  if (!enTauri()) return;
  const [{ emitTo }, { invoke }] = await Promise.all([import("@tauri-apps/api/event"), import("@tauri-apps/api/core")]);
  await emitTo("main", "navegar", { seccion, reunion });
  await invoke("abrir_principal");
}

async function ocultar() {
  if (enTauri()) (await import("@tauri-apps/api/window")).getCurrentWindow().hide();
}

export function Panel() {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [recientes, setRecientes] = useState<ReunionResumen[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refrescar = useCallback(async () => {
    try {
      setEstado(await api<Estado>("/api/estado"));
      setRecientes(await api<ReunionResumen[]>("/api/reuniones?limite=3"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refrescar();
    // Cada vez que se muestra el panel, datos frescos.
    const quitar = enTauri()
      ? import("@tauri-apps/api/window").then(({ getCurrentWindow }) =>
          getCurrentWindow().onFocusChanged(({ payload: enfocado }) => enfocado && refrescar()),
        )
      : Promise.resolve(() => {});
    const t = window.setInterval(refrescar, 5000);
    return () => {
      window.clearInterval(t);
      quitar.then((f) => f());
    };
  }, [refrescar]);

  useEventos(
    useCallback(
      (e) =>
        e.tipo === "ahora"
          ? setEstado((s) => (s ? { ...s, ahora: e.datos } : s))
          : e.tipo !== "dictado" && refrescar(), // la píldora dibuja el dictado, diez veces por segundo
      [refrescar],
    ),
  );

  useEffect(() => {
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && ocultar();
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, []);

  const ahora = estado?.ahora;
  const pausar = async () => {
    await api("/api/pausa", { metodo: "POST", cuerpo: { pausado: !ahora?.pausado } });
    refrescar();
  };

  return (
    <div className="flex h-full flex-col gap-4 border border-linea bg-fondo p-4 text-[13.5px]">
      {/* Franja para arrastrar el panel: Tauri solo arrastra desde el elemento marcado, no desde sus hijos. */}
      <div data-tauri-drag-region title="Arrastrar para mover" className="-mx-4 -mt-4 flex cursor-move items-baseline justify-between px-4 pt-4 pb-1">
        <span className="pointer-events-none font-serif text-[20px] italic">Minuta</span>
        <span className="pointer-events-none text-[12px] text-tenue">{error ? "sin motor" : ahora?.pausado ? "en pausa" : "activa"}</span>
      </div>

      <section className="flex flex-col gap-1.5 rounded-[10px] border border-linea bg-papel p-3">
        {error ? (
          <p className="m-0 text-tenue">{error}</p>
        ) : ahora?.grabando ? (
          <>
            <Rotulo acento>
              <span className="inline-flex items-center gap-2">
                <PuntoGrabando /> Grabando · {mmss(ahora.segundos)}
              </span>
            </Rotulo>
            <p className="m-0 line-clamp-2 font-serif text-[16px] leading-snug">
              {ahora.manual ? ahora.titulo || "Reunión presencial" : ahora.ventanas?.[0] ?? "Reunión en curso"}
            </p>
            <div className="mt-1 flex gap-4 text-[12.5px]">
              <button
                onClick={async () => {
                  await marcarAhora();
                  refrescar();
                }}
                title={`Marcar este momento (${ATAJO_MARCA})`}
                className="inline-flex items-center gap-1.5 rounded-md text-tinta underline decoration-borde underline-offset-4 hover:decoration-tinta"
              >
                <Star size={13} aria-hidden /> Marcar{ahora.marcas?.length ? ` (${ahora.marcas.length})` : ""}
              </button>
              <button
                onClick={async () => {
                  await api("/api/grabacion-actual/cortar", { metodo: "POST" });
                  refrescar();
                }}
                className="inline-flex items-center gap-1.5 rounded-md text-tenue underline decoration-borde underline-offset-4 hover:text-tinta hover:decoration-tinta"
              >
                <Scissors size={13} aria-hidden /> {ahora.manual ? "Terminar" : "Cortar acá"}
              </button>
            </div>
            <div className="mt-1.5">
              <Anotar compacto alAnotar={refrescar} />
            </div>
          </>
        ) : (
          <>
            <Rotulo>{ahora?.pausado ? "En pausa" : "En espera"}</Rotulo>
            <p className="m-0 leading-snug text-tenue">
              {ahora?.pausado ? "No se graba nada hasta que la reanudes." : "Graba sola cuando Chrome, Edge o Slack toman el micrófono."}
            </p>
            {!ahora?.pausado && ahora?.sin_deteccion && (
              <p role="alert" className="m-0 font-medium leading-snug text-acento">No veo quién usa el micrófono: grabá las reuniones a mano.</p>
            )}
            {!error && <GrabarAMano compacto alEmpezar={() => refrescar()} />}
          </>
        )}
      </section>

      {!!estado?.para_revisar && (
        <button onClick={() => abrir("revisar")} className="rounded-md py-1 text-left underline decoration-borde underline-offset-4 hover:decoration-tinta">
          {estado.para_revisar === 1 ? "1 grabación para revisar" : `${estado.para_revisar} grabaciones para revisar`}
        </button>
      )}

      {ahora?.grabando && <Alertas alertas={ahora.alertas} compacta alActuar={refrescar} />}

      <section className="flex min-h-0 flex-1 flex-col gap-1 overflow-hidden">
        <Rotulo>Últimas reuniones</Rotulo>
        {recientes.map((r) => (
          <button key={r.id} onClick={() => abrir("reuniones", r.id)} className="flex items-baseline gap-2 rounded-md px-1.5 py-1.5 text-left hover:bg-papel">
            <span className="font-mono text-[11.5px] text-tenue">{horaDe(r.inicio)}</span>
            <span className="truncate font-serif text-[15px]">{r.titulo}</span>
          </button>
        ))}
      </section>

      <div className="flex gap-2">
        <Boton onClick={pausar} disabled={!!error} className="flex-1">
          {ahora?.pausado ? <Play size={15} aria-hidden /> : <Pause size={15} aria-hidden />}
          {ahora?.pausado ? "Reanudar" : "Pausar"}
        </Boton>
        <Boton variante="primario" onClick={() => abrir("reuniones")} className="flex-1">
          <AppWindow size={15} aria-hidden /> Abrir Minuta
        </Boton>
      </div>
    </div>
  );
}
