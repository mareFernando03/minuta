// Ctrl+/ muestra todos los atajos, agrupados por dónde funcionan.

import { useEffect, useRef } from "react";
import { X } from "lucide-react";

const GRUPOS: { titulo: string; atajos: [string, string][] }[] = [
  {
    titulo: "Desde cualquier aplicación",
    atajos: [
      ["Ctrl+Alt+Shift+M", "Marcar este momento de la reunión"],
      ["Ctrl+Alt+Shift+P", "Pausar o reanudar las grabaciones"],
      ["Ctrl+Alt+Shift+G", "Grabar a mano una reunión presencial"],
    ],
  },
  {
    titulo: "En Minuta",
    atajos: [
      ["Ctrl+K", "Paleta de comandos: ir a una sección o buscar en las reuniones"],
      ["Ctrl+1 … Ctrl+5", "Ahora, Reuniones, Para revisar, Motores, Glosario"],
      ["Alt (mantener)", "Ver los atajos sobre los botones"],
      ["Ctrl+,", "Ajustes"],
      ["Ctrl+Shift+P", "Pausar o reanudar las grabaciones"],
      ["Ctrl+/", "Esta lista"],
    ],
  },
  {
    titulo: "Reuniones",
    atajos: [
      ["/", "Buscar en todas las reuniones"],
      ["↑ ↓ o J K", "Moverse por la lista"],
    ],
  },
  {
    titulo: "Una reunión",
    atajos: [
      ["Espacio", "Reproducir o pausar"],
      ["J / L", "15 segundos atrás o adelante"],
      ["Ctrl+F", "Buscar en esta reunión (Enter y Shift+Enter para moverse)"],
      ["] / [", "Palabra dudosa siguiente o anterior"],
      ["Clic en una palabra", "Escuchar desde ahí"],
      ["Doble clic en un turno", "Corregirlo (Enter guarda, Esc cancela)"],
      ["Ctrl+C", "Copiar lo seleccionado con minuto y quién habla"],
    ],
  },
];

export function HojaAtajos({ abierta, cerrar }: { abierta: boolean; cerrar: () => void }) {
  const dialogo = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = dialogo.current;
    if (!d) return;
    if (abierta && !d.open) d.showModal();
    if (!abierta && d.open) d.close();
  }, [abierta]);
  return (
    <dialog
      ref={dialogo}
      onClose={cerrar}
      onClick={(e) => e.target === dialogo.current && cerrar()}
      aria-labelledby="titulo-atajos"
      className="m-auto w-[min(640px,calc(100vw-32px))] rounded-xl border border-linea bg-fondo p-0 text-tinta shadow-flotante backdrop:bg-black/30"
    >
      <div className="flex max-h-[80vh] flex-col gap-6 overflow-y-auto p-7">
        <div className="flex items-baseline justify-between">
          <h2 id="titulo-atajos" className="m-0 font-serif text-[26px] font-medium">
            Atajos
          </h2>
          <button aria-label="Cerrar (Esc)" onClick={cerrar} className="grid h-8 w-8 place-items-center rounded-md hover:bg-papel">
            <X size={16} aria-hidden />
          </button>
        </div>
        {GRUPOS.map((g) => (
          <section key={g.titulo} className="flex flex-col gap-1">
            <h3 className="m-0 text-[11.5px] font-medium uppercase tracking-[0.08em] text-tenue">{g.titulo}</h3>
            <dl className="m-0 grid grid-cols-[minmax(150px,auto)_1fr] gap-x-6">
              {g.atajos.map(([tecla, que]) => (
                <div key={tecla} className="contents">
                  <dt className="border-t border-linea py-2">
                    <kbd className="rounded border border-borde px-1.5 py-0.5 font-mono text-[12px]">{tecla}</kbd>
                  </dt>
                  <dd className="m-0 border-t border-linea py-2 text-[13.5px]">{que}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </dialog>
  );
}
