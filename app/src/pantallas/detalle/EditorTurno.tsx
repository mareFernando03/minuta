// Edición en línea de un turno: Enter guarda, Esc cancela, salir del campo guarda.

import { useLayoutEffect, useRef, useState } from "react";

export function EditorTurno({ texto, guardar, cancelar }: { texto: string; guardar: (nuevo: string) => void; cancelar: () => void }) {
  const [valor, setValor] = useState(texto);
  const campo = useRef<HTMLTextAreaElement>(null);
  const terminado = useRef(false);

  useLayoutEffect(() => {
    const c = campo.current;
    if (!c) return;
    c.style.height = "auto";
    c.style.height = `${c.scrollHeight}px`;
  }, [valor]);

  useLayoutEffect(() => {
    campo.current?.focus();
    campo.current?.select();
  }, []);

  const terminar = (guardando: boolean) => {
    if (terminado.current) return;
    terminado.current = true;
    const nuevo = valor.replace(/\s+/g, " ").trim();
    if (guardando && nuevo && nuevo !== texto) guardar(nuevo);
    else cancelar();
  };

  return (
    <textarea
      ref={campo}
      value={valor}
      aria-label="Corregir el turno (Enter guarda, Esc cancela)"
      onChange={(e) => setValor(e.target.value)}
      onBlur={() => terminar(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) (e.preventDefault(), terminar(true));
        if (e.key === "Escape") (e.preventDefault(), terminar(false));
      }}
      rows={1}
      className="m-0 -mx-2 -my-1 w-[calc(100%+16px)] resize-none rounded-md border border-borde bg-fondo px-2 py-1 font-serif text-[18.5px] leading-[1.65] outline-none"
    />
  );
}
