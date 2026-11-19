// Tooltip propio: aparece con retardo al pasar el mouse o al llegar con el teclado, muestra el atajo y queda
// asociado al control (aria-describedby). Reemplaza al `title` nativo, que tarda, no sale con teclado y no se
// puede estilar.

import { cloneElement, isValidElement, useId, useRef, useState, type ReactElement } from "react";

const RETARDO_MS = 450;

export function Ayuda({
  texto,
  atajo,
  children,
  lado = "abajo",
  alinear = "centro",
}: {
  texto: string;
  atajo?: string;
  children: ReactElement;
  lado?: "arriba" | "abajo";
  alinear?: "centro" | "derecha"; // «derecha» para controles pegados al borde: el tooltip no se sale de la pantalla
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  const temporizador = useRef<number | undefined>(undefined);
  const mostrar = () => {
    window.clearTimeout(temporizador.current);
    temporizador.current = window.setTimeout(() => setVisible(true), RETARDO_MS);
  };
  const ocultar = () => {
    window.clearTimeout(temporizador.current);
    setVisible(false);
  };
  const hijo = isValidElement(children) ? cloneElement(children as ReactElement<Record<string, unknown>>, { "aria-describedby": id }) : children;
  return (
    <span className="relative inline-flex" onMouseEnter={mostrar} onMouseLeave={ocultar} onFocus={mostrar} onBlur={ocultar} onKeyDown={(e) => e.key === "Escape" && ocultar()}>
      {hijo}
      <span
        role="tooltip"
        id={id}
        className={`pointer-events-none absolute z-40 whitespace-nowrap ${alinear === "derecha" ? "right-0" : "left-1/2 -translate-x-1/2"} rounded-md bg-tinta px-2 py-1 text-[12px] text-fondo shadow-flotante transition-opacity duration-150 ${
          lado === "abajo" ? "top-full mt-1.5" : "bottom-full mb-1.5"
        } ${visible ? "visible opacity-100" : "invisible opacity-0"}`}
      >
        {texto}
        {atajo && <kbd className="ml-1.5 font-mono text-[11px] opacity-70">{atajo}</kbd>}
      </span>
    </span>
  );
}
