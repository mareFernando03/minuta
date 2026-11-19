// Píldora del dictado (Ctrl+Win sostenido): una ventana chica, sin marco y sin foco, abajo al centro del monitor.
// La muestra y la oculta Rust (src-tauri/src/dictado.rs). Dibuja lo que avisa el motor por el evento «dictado»
// (escuchando con el nivel, transcribiendo, sin voz, error) y lo que solo sabe Rust por «dictado-aviso» (que no se
// pudo pegar). Cada dictado nuevo arranca de cero: la ventana no se destruye entre uno y otro.

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic } from "lucide-react";
import { enTauri, useEventos } from "./api/cliente";
import { Medidor } from "./componentes/Basicos";
import { mmss } from "./util/formato";

type Fase =
  | { estado: "escuchando"; nivel: number }
  | { estado: "transcribiendo" }
  | { estado: "aviso"; mensaje: string; error: boolean };

export function Dictado() {
  const [fase, setFase] = useState<Fase>({ estado: "escuchando", nivel: 0 });
  const [segundos, setSegundos] = useState(0);
  const desde = useRef<number | null>(null);

  useEffect(() => {
    // La ventana es transparente: solo se ve la píldora, no el fondo de la app.
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
  }, []);

  // El contador corre solo mientras se escucha.
  useEffect(() => {
    if (fase.estado !== "escuchando") return;
    const t = window.setInterval(() => desde.current && setSegundos((Date.now() - desde.current) / 1000), 250);
    return () => window.clearInterval(t);
  }, [fase.estado]);

  useEventos(
    useCallback((e) => {
      if (e.tipo !== "dictado") return;
      const d = e.datos;
      if (d.estado === "escuchando") {
        if (desde.current === null) {
          desde.current = Date.now(); // un dictado nuevo: el contador vuelve a cero
          setSegundos(0);
        }
        setFase({ estado: "escuchando", nivel: d.nivel ?? 0 });
      } else if (d.estado === "transcribiendo") {
        desde.current = null;
        setFase({ estado: "transcribiendo" });
      } else if (d.estado === "listo" && d.vacio) {
        desde.current = null;
        setFase({ estado: "aviso", mensaje: "No te escuché", error: false });
      } else if (d.estado === "listo" || d.estado === "cancelado") {
        desde.current = null;
        setFase({ estado: "escuchando", nivel: 0 }); // la ventana ya se oculta: queda lista para el próximo
      } else if (d.estado === "error") {
        desde.current = null;
        setFase({ estado: "aviso", mensaje: d.mensaje, error: true });
      }
    }, []),
  );

  useEffect(() => {
    if (!enTauri()) return;
    const quitar = import("@tauri-apps/api/event").then(({ listen }) =>
      listen<string>("dictado-aviso", ({ payload }) => setFase({ estado: "aviso", mensaje: payload, error: false })),
    );
    return () => {
      quitar.then((f) => f());
    };
  }, []);

  return (
    <div className="flex h-full items-center justify-center p-1" aria-live="polite">
      <div className="flex h-9 w-full items-center gap-2.5 rounded-full border border-borde bg-fondo px-3.5 shadow-flotante">
        {fase.estado === "escuchando" && (
          <>
            <Mic size={15} className="shrink-0 text-acento" aria-hidden />
            <div className="min-w-0 flex-1">
              <Medidor nivel={fase.nivel} etiqueta="Nivel del micrófono" />
            </div>
            <span className="shrink-0 font-mono text-[12px] tabular-nums text-tenue">{mmss(segundos)}</span>
          </>
        )}
        {fase.estado === "transcribiendo" && (
          <>
            <Loader2 size={15} className="shrink-0 animate-spin text-tenue motion-reduce:animate-none" aria-hidden />
            <span className="text-[13px] text-tenue">Transcribiendo…</span>
          </>
        )}
        {fase.estado === "aviso" && (
          <span className={`truncate text-[13px] ${fase.error ? "text-acento" : "text-tinta"}`} title={fase.mensaje}>
            {fase.mensaje}
          </span>
        )}
      </div>
    </div>
  );
}
