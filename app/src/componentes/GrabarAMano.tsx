// Grabar a mano: para las reuniones presenciales, donde ninguna app toma el micrófono y el vigía no arranca solo.
// Título y carpeta son opcionales; con carpeta, la reunión queda identificada y sigue la regla de privacidad de esa
// carpeta. Sin carpeta ni evento en el calendario, va a «Para revisar» y no sale de la PC. Graba aunque Minuta esté
// en pausa, y la pausa sigue al terminar.
// No usa los avisos: también vive en el panel de la bandeja, que no los tiene. El error se muestra acá mismo.

import { useState } from "react";
import { Mic } from "lucide-react";
import { api, useDatos } from "../api/cliente";
import type { Ajustes } from "../api/tipos";
import { Boton } from "./Basicos";
import { Ayuda } from "./Ayuda";

export const ATAJO_GRABAR = "Ctrl+Alt+Shift+G";

export function GrabarAMano({ alEmpezar, compacto = false }: { alEmpezar: (mensaje: string) => void; compacto?: boolean }) {
  const [error, setError] = useState<string | null>(null);
  const { datos: ajustes } = useDatos<Ajustes>("/api/ajustes");
  const [titulo, setTitulo] = useState("");
  const [carpeta, setCarpeta] = useState("");
  const [enviando, setEnviando] = useState(false);

  const grabar = async (e: React.FormEvent) => {
    e.preventDefault();
    setEnviando(true);
    setError(null);
    try {
      await api("/api/grabacion-actual/empezar", { metodo: "POST", cuerpo: { titulo: titulo.trim() || null, carpeta: carpeta || null } });
      setTitulo("");
      alEmpezar(carpeta ? `Grabando a mano en ${carpeta}` : "Grabando a mano. Si no está en el calendario, queda para revisar");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setEnviando(false);
    }
  };

  const campo = "rounded-lg border border-borde bg-fondo px-3 text-tinta outline-none focus-visible:border-tinta";
  return (
    <form onSubmit={grabar} aria-label="Grabar a mano" className={`flex flex-wrap items-end ${compacto ? "gap-2" : "gap-3"}`}>
      <label className={`flex min-w-0 flex-1 flex-col gap-1.5 text-tenue ${compacto ? "text-[12px]" : "min-w-56 text-[12.5px]"}`}>
        Título (opcional)
        <input
          value={titulo}
          onChange={(e) => setTitulo(e.target.value)}
          maxLength={200}
          placeholder="Charla con…"
          className={`${campo} ${compacto ? "py-1.5 text-[13px]" : "py-2 text-[14px]"}`}
        />
      </label>
      <label className={`flex flex-col gap-1.5 text-tenue ${compacto ? "text-[12px]" : "text-[12.5px]"}`}>
        Carpeta
        <select
          value={carpeta}
          onChange={(e) => setCarpeta(e.target.value)}
          className={`${campo} ${compacto ? "py-1.5 text-[13px]" : "py-2 text-[14px]"}`}
        >
          <option value="">Sin carpeta</option>
          {(ajustes?.carpetas ?? []).map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </label>
      <Ayuda texto="Grabar a mano, también desde cualquier aplicación" atajo={ATAJO_GRABAR}>
        <Boton variante="primario" type="submit" disabled={enviando} className={compacto ? "" : "min-h-11 px-5"}>
          <Mic size={14} aria-hidden /> Grabar ahora
        </Boton>
      </Ayuda>
      {error && (
        <p role="alert" className="m-0 w-full text-[12.5px] text-acento">
          {error}
        </p>
      )}
    </form>
  );
}
