import type { ReactNode } from "react";

export function PuntoGrabando({ grande = false }: { grande?: boolean }) {
  return <span aria-hidden="true" className={`latido inline-block rounded-full bg-acento ${grande ? "h-[9px] w-[9px]" : "h-[7px] w-[7px]"}`} />;
}

export function Rotulo({ children, acento = false }: { children: ReactNode; acento?: boolean }) {
  return <span className={`text-[11.5px] font-medium uppercase tracking-[0.08em] ${acento ? "text-acento" : "text-tenue"}`}>{children}</span>;
}

type VarianteBoton = "primario" | "secundario" | "fantasma";

export function Boton({
  children,
  variante = "secundario",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variante?: VarianteBoton }) {
  const estilos: Record<VarianteBoton, string> = {
    primario: "bg-tinta text-fondo hover:opacity-90",
    secundario: "border border-borde text-tinta hover:bg-papel",
    fantasma: "text-tinta hover:bg-papel",
  };
  return (
    <button
      {...props}
      className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-[7px] px-3.5 text-[13.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${estilos[variante]} ${className}`}
    >
      {children}
    </button>
  );
}

export function Vacio({ titulo, children }: { titulo: string; children?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-20 text-center">
      <p className="font-serif text-[22px] leading-tight">{titulo}</p>
      {children && <div className="text-[13.5px] leading-relaxed text-tenue">{children}</div>}
    </div>
  );
}

export function ErrorCaja({ mensaje, reintentar }: { mensaje: string; reintentar?: () => void }) {
  return (
    <div role="alert" className="m-6 flex items-start justify-between gap-4 rounded-lg border border-linea bg-acento-suave px-4 py-3 text-[13.5px]">
      <span>{mensaje}</span>
      {reintentar && (
        <button onClick={reintentar} className="shrink-0 underline underline-offset-4">
          Reintentar
        </button>
      )}
    </div>
  );
}

export function Cargando() {
  return (
    <div aria-busy="true" className="flex flex-col gap-4 p-6">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-14 animate-pulse rounded-lg bg-papel" />
      ))}
    </div>
  );
}

export function Medidor({ nivel, etiqueta }: { nivel: number; etiqueta: string }) {
  // RMS de voz ronda 0,02-0,2: escala logarítmica para que se mueva al hablar
  const barras = 32;
  const llenas = nivel <= 0 ? 0 : Math.min(barras, Math.round(((Math.log10(nivel) + 2.3) / 1.8) * barras));
  return (
    <div role="meter" aria-label={etiqueta} aria-valuemin={0} aria-valuemax={barras} aria-valuenow={llenas} className="flex h-[22px] items-end gap-[3px]">
      {Array.from({ length: barras }, (_, i) => (
        <span key={i} className={`flex-1 rounded-[1px] transition-[height] duration-100 ${i < llenas ? "bg-tinta" : "bg-linea"}`} style={{ height: `${35 + ((i * 37) % 60)}%` }} />
      ))}
    </div>
  );
}
