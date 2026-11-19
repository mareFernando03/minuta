// Grabaciones que todavía no son reunión: transcribiéndose (con avance real y cuánto falta), en cola, esperando
// (a batería, notebook en uso) o con error y su explicación. Se muestra arriba de la biblioteca y en «Ahora».

import { RotateCcw } from "lucide-react";
import { api } from "../api/cliente";
import type { Estado, Trabajo } from "../api/tipos";
import { faltan, horaDe, tituloProvisorio } from "../util/formato";
import { useAvisos } from "./Avisos";
import { Boton } from "./Basicos";

function primeraMayuscula(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function situacion(t: Trabajo) {
  if (t.estado === "corriendo") {
    const pct = t.avance != null ? ` · ${Math.round(t.avance * 100)} %` : "";
    const resto = faltan(t.restante_s);
    return `${primeraMayuscula(t.progreso ?? "procesando")}${pct}${resto ? ` · ${resto}` : ""}`;
  }
  if (t.error) return `Reintentando: ${t.error}`;
  if (t.progreso?.startsWith("esperando:")) return primeraMayuscula(t.progreso.replace("esperando:", "Esperando:"));
  return ESPERANDO[t.tipo] ?? "En cola";
}

const ESPERANDO: Record<string, string> = {
  repasar: "Repaso con más calidad, cuando la notebook esté libre",
  diarizar: "Separar las voces de los demás, en cola",
  resumir: "Resumen con Claude, en cola",
};

export function EnProceso({ estado, alCambiar, compacto = false }: { estado: Estado | null; alCambiar: () => void; compacto?: boolean }) {
  const { avisar } = useAvisos();
  const cola = estado?.cola ?? [];
  const errores = estado?.errores ?? [];
  if (!cola.length && !errores.length) return null;

  const reintentar = async (gid: string) => {
    try {
      await api(`/api/grabaciones/${gid}/reprocesar`, { metodo: "POST", cuerpo: {} });
      avisar("Se vuelve a intentar con la regla de su carpeta");
      alCambiar();
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };

  return (
    <section aria-label="En proceso" className={`flex flex-col ${compacto ? "gap-2.5" : "mx-3 mb-2 mt-3 gap-1 rounded-[10px] border border-linea p-1"}`}>
      {cola.map((t) => (
        <div key={t.id} className={`flex flex-col gap-1.5 ${compacto ? "" : "rounded-lg px-3 py-2.5"}`}>
          <span className="flex items-baseline justify-between gap-3">
            <span className="truncate font-serif text-[16.5px]">{t.titulo ?? tituloProvisorio(t.ventanas, t.apps)}</span>
            {t.inicio && <span className="shrink-0 font-mono text-[11.5px] text-tenue">{horaDe(t.inicio)}</span>}
          </span>
          <span className={`text-[12.5px] ${t.error ? "text-acento" : "text-tenue"}`}>{situacion(t)}</span>
          {t.estado === "corriendo" && t.avance != null && (
            <span
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(t.avance * 100)}
              aria-label="Avance"
              className="block h-[3px] overflow-hidden rounded-full bg-linea"
            >
              <span className="block h-full rounded-full bg-tinta transition-[width] duration-700" style={{ width: `${Math.max(3, t.avance * 100)}%` }} />
            </span>
          )}
        </div>
      ))}
      {errores.map((g) => (
        <div key={g.grabacion_id} className={`flex flex-col gap-1.5 ${compacto ? "" : "rounded-lg px-3 py-2.5"}`}>
          <span className="flex items-baseline justify-between gap-3">
            <span className="truncate font-serif text-[16.5px]">{g.titulo ?? tituloProvisorio(g.ventanas, g.apps)}</span>
            {g.inicio && <span className="shrink-0 font-mono text-[11.5px] text-tenue">{horaDe(g.inicio)}</span>}
          </span>
          <span className="text-[12.5px] text-acento">No se pudo procesar: {g.error ?? "error desconocido"}</span>
          <Boton className="w-fit" onClick={() => reintentar(g.grabacion_id)}>
            <RotateCcw size={13} aria-hidden /> Reintentar
          </Boton>
        </div>
      ))}
    </section>
  );
}
