// Antes de entrar a una reunión del calendario: lo que quedó de la anterior de su serie (resumen, acciones abiertas
// y marcas). Se arma sin LLM con lo que ya está en la biblioteca. Aparece desde 15 minutos antes.

import { CalendarClock } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/cliente";
import type { Brief as B } from "../api/tipos";
import { fechaCorta, horaDe } from "../util/formato";
import { nombreMarca } from "./Marcas";
import { Rotulo } from "./Basicos";

export function Brief({ abrir }: { abrir?: (id: string) => void }) {
  const [b, setB] = useState<B | null>(null);
  const cargar = useCallback(() => api<B | null>("/api/brief").then(setB).catch(() => setB(null)), []);

  useEffect(() => {
    cargar();
    const t = window.setInterval(() => !document.hidden && cargar(), 60_000); // oculta en la bandeja, avisa el evento
    const alBrief = () => cargar();
    window.addEventListener("minuta:brief", alBrief);
    return () => {
      window.clearInterval(t);
      window.removeEventListener("minuta:brief", alBrief);
    };
  }, [cargar]);

  if (!b) return null;
  const minutos = Math.max(0, Math.round((new Date(b.evento.inicio).getTime() - Date.now()) / 60_000));
  const a = b.anterior;
  return (
    <section aria-label="Tu próxima reunión" className="flex flex-col gap-3 rounded-[10px] border border-linea bg-papel p-5">
      <Rotulo>
        <span className="inline-flex items-center gap-2">
          <CalendarClock size={14} aria-hidden /> {minutos <= 1 ? "Empieza ya" : `Empieza en ${minutos} min`} · {horaDe(b.evento.inicio)}
        </span>
      </Rotulo>
      <h2 className="m-0 font-serif text-[24px] font-medium leading-tight">{b.evento.titulo}</h2>
      {!a ? (
        <p className="m-0 text-[13.5px] text-tenue">No hay una reunión anterior de esta serie en la biblioteca.</p>
      ) : (
        <div className="flex flex-col gap-3 text-[14px]">
          <p className="m-0 text-[13px] text-tenue">
            La última vez, el {fechaCorta(a.inicio)}:{" "}
            {abrir && (
              <button onClick={() => abrir(a.id)} className="underline decoration-borde underline-offset-4 hover:text-tinta hover:decoration-tinta">
                {a.titulo}
              </button>
            )}
          </p>
          {a.resumen.length > 0 && (
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5 font-serif text-[15.5px] leading-snug">
              {a.resumen.map((v, i) => (
                <li key={i}>{v}</li>
              ))}
            </ul>
          )}
          {a.acciones.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-[12px] font-medium uppercase tracking-[0.08em] text-tenue">Acciones abiertas</span>
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {a.acciones.map((x) => (
                  <li key={x.id}>
                    ☐ {x.texto}
                    {x.responsable && <span className="text-tenue"> · {x.responsable}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {a.marcas.length > 0 && (
            <p className="m-0 text-[13px] text-tenue">
              Marcaste: {a.marcas.map((m) => (m.nota ? `${nombreMarca(m)}: ${m.nota}` : nombreMarca(m))).join(" · ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
