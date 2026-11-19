// Bloc de notas durante la reunión: cada línea queda con su minuto. Una nota es una marca con texto, así que pasa
// a la reunión, aparece en el margen de la transcripción y el resumen la toma como lo que más importó.

import { useState } from "react";
import { api } from "../api/cliente";
import type { Marca } from "../api/tipos";
import { mmss } from "../util/formato";

export function Anotar({ alAnotar, compacto = false }: { alAnotar: (m: Marca) => void; compacto?: boolean }) {
  const [texto, setTexto] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ultima, setUltima] = useState<Marca | null>(null);

  const guardar = async () => {
    const nota = texto.trim();
    if (!nota) return;
    try {
      const m = await api<Marca>("/api/grabacion-actual/marca", { metodo: "POST", cuerpo: { nota } });
      setTexto("");
      setError(null);
      setUltima(m);
      alAnotar(m);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); // el texto queda en el campo: no se pierde
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <input
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && guardar()}
        maxLength={2000}
        placeholder={compacto ? "Anotar algo (Enter)" : "Anotar algo de este momento y Enter. Queda con su minuto."}
        aria-label="Nota de este momento"
        className={`w-full rounded-lg border border-borde bg-fondo outline-none focus:border-tinta ${compacto ? "px-2.5 py-1.5 text-[13px]" : "px-3.5 py-2.5 text-[15px]"}`}
      />
      <span role="status" className={`min-h-[1.2em] text-[12px] ${error ? "text-acento" : "text-tenue"}`}>
        {error ?? (ultima ? `Anotado en ${mmss(ultima.t)}` : "")}
      </span>
    </div>
  );
}
