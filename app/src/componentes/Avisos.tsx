// Avisos con «Deshacer»: las acciones destructivas esperan 10 s antes de ejecutarse, en lugar de preguntar
// «¿estás seguro?». Si la persona deshace, la acción nunca llega al motor.

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

export interface Accion {
  nombre: string;
  hacer: () => unknown;
}

interface Aviso {
  id: number;
  texto: string;
  tono?: "normal" | "error";
  deshacer?: () => void;
  acciones?: Accion[];
}

interface Contexto {
  avisar: (texto: string, tono?: "normal" | "error") => void;
  conDeshacer: (texto: string, ejecutar: () => Promise<unknown>, alDeshacer?: () => void) => void;
  /** Aviso con botones propios («Cambiar también las otras 7», «Agregar al glosario»). Se va solo a los 12 s. */
  conAcciones: (texto: string, acciones: Accion[]) => void;
}

const Ctx = createContext<Contexto | null>(null);
const DESHACER_MS = 10000;

export function ProveedorAvisos({ children }: { children: ReactNode }) {
  const [avisos, setAvisos] = useState<Aviso[]>([]);
  const sec = useRef(0);

  const quitar = useCallback((id: number) => setAvisos((a) => a.filter((x) => x.id !== id)), []);

  const avisar = useCallback(
    (texto: string, tono: "normal" | "error" = "normal") => {
      const id = ++sec.current;
      setAvisos((a) => [...a, { id, texto, tono }]);
      window.setTimeout(() => quitar(id), tono === "error" ? 8000 : 4000);
    },
    [quitar],
  );

  const conDeshacer = useCallback(
    (texto: string, ejecutar: () => Promise<unknown>, alDeshacer?: () => void) => {
      const id = ++sec.current;
      const temporizador = window.setTimeout(async () => {
        quitar(id);
        try {
          await ejecutar();
        } catch (e) {
          alDeshacer?.();
          avisar(e instanceof Error ? e.message : String(e), "error");
        }
      }, DESHACER_MS);
      setAvisos((a) => [
        ...a,
        {
          id,
          texto,
          deshacer: () => {
            window.clearTimeout(temporizador);
            quitar(id);
            alDeshacer?.();
          },
        },
      ]);
    },
    [avisar, quitar],
  );

  const conAcciones = useCallback(
    (texto: string, acciones: Accion[]) => {
      const id = ++sec.current;
      setAvisos((a) => [...a, { id, texto, acciones }]);
      window.setTimeout(() => quitar(id), 12000);
    },
    [quitar],
  );

  return (
    <Ctx.Provider value={{ avisar, conDeshacer, conAcciones }}>
      {children}
      <div role="status" aria-live="polite" className="pointer-events-none fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2">
        {avisos.map((a) => (
          <div
            key={a.id}
            className={`aparecer pointer-events-auto flex items-center gap-4 rounded-lg px-4 py-3 text-[13.5px] shadow-flotante ${
              a.tono === "error" ? "bg-acento text-fondo" : "bg-tinta text-fondo"
            }`}
          >
            <span>{a.texto}</span>
            {a.deshacer && (
              <button onClick={a.deshacer} className="font-medium underline underline-offset-4">
                Deshacer
              </button>
            )}
            {a.acciones?.map((ac) => (
              <button
                key={ac.nombre}
                onClick={async () => {
                  quitar(a.id);
                  try {
                    await ac.hacer();
                  } catch (e) {
                    avisar(e instanceof Error ? e.message : String(e), "error");
                  }
                }}
                className="font-medium underline underline-offset-4"
              >
                {ac.nombre}
              </button>
            ))}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useAvisos() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAvisos fuera de ProveedorAvisos");
  return c;
}
