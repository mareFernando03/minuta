// Barra lateral: el buscador (Ctrl+K) arriba, lo de todos los días después y lo de vez en cuando abajo. Se puede
// achicar a una tira de íconos para darle lugar a la transcripción en pantallas chicas.

import { BookOpen, Cpu, Inbox, MessagesSquare, PanelLeftClose, PanelLeftOpen, Radio, Search, Settings, type LucideIcon } from "lucide-react";
import { useState } from "react";
import type { Estado, Motores } from "../api/tipos";
import { leerPreferencia, guardarPreferencia } from "../util/preferencias";
import { mmss, usd } from "../util/formato";
import { PuntoGrabando } from "./Basicos";

export type Seccion = "ahora" | "reuniones" | "revisar" | "motores" | "glosario" | "ajustes";

const ARRIBA: { id: Seccion; nombre: string; atajo: string; icono: LucideIcon }[] = [
  { id: "ahora", nombre: "Ahora", atajo: "Ctrl+1", icono: Radio },
  { id: "reuniones", nombre: "Reuniones", atajo: "Ctrl+2", icono: MessagesSquare },
  { id: "revisar", nombre: "Para revisar", atajo: "Ctrl+3", icono: Inbox },
];
const ABAJO: { id: Seccion; nombre: string; atajo: string; icono: LucideIcon }[] = [
  { id: "motores", nombre: "Motores", atajo: "Ctrl+4", icono: Cpu },
  { id: "glosario", nombre: "Glosario", atajo: "Ctrl+5", icono: BookOpen },
  { id: "ajustes", nombre: "Ajustes", atajo: "Ctrl+,", icono: Settings },
];

export function Barra({
  actual,
  ir,
  estado,
  motores,
  buscar,
}: {
  actual: Seccion;
  ir: (s: Seccion) => void;
  estado: Estado | null;
  motores: Motores | null;
  buscar: () => void;
}) {
  const [chica, setChica] = useState(() => leerPreferencia("barra-chica") === "1");
  const ahora = estado?.ahora;
  const alternar = () => {
    setChica((c) => (guardarPreferencia("barra-chica", c ? "0" : "1"), !c));
  };

  const item = (s: (typeof ARRIBA)[number]) => (
    <li key={s.id}>
      <button
        onClick={() => ir(s.id)}
        aria-current={actual === s.id ? "page" : undefined}
        aria-label={chica ? s.nombre : undefined}
        title={chica ? `${s.nombre} (${s.atajo})` : s.atajo}
        className={`relative flex w-full items-center gap-2.5 rounded-md py-[9px] text-left transition-colors ${chica ? "justify-center px-0" : "px-2.5"} ${
          actual === s.id ? "bg-seleccion font-medium" : "hover:bg-seleccion/60"
        }`}
      >
        <s.icono size={15} aria-hidden className={actual === s.id ? "text-tinta" : "text-tenue"} />
        {!chica && <span className="flex-1">{s.nombre}</span>}
        {!chica && s.id === "ahora" && ahora?.grabando && (
          <span className="flex items-center gap-1.5 font-mono text-xs text-acento">
            <PuntoGrabando />
            {mmss(ahora.segundos)}
          </span>
        )}
        {chica && s.id === "ahora" && ahora?.grabando && <PuntoGrabando />}
        {!chica && s.id === "ahora" && ahora?.pausado && <span className="text-xs text-tenue">en pausa</span>}
        {!chica && s.id === "revisar" && !!estado?.para_revisar && <span className="text-xs text-tenue">{estado.para_revisar}</span>}
        {!chica && s.id === "motores" && !!estado?.con_error && <span className="text-xs font-medium text-acento">{estado.con_error} con error</span>}
      </button>
    </li>
  );

  return (
    <nav aria-label="Secciones" className={`flex shrink-0 flex-col gap-5 border-r border-linea bg-papel py-6 transition-[width] duration-150 ${chica ? "w-[60px] px-2" : "w-[224px] px-3.5"}`}>
      {!chica && <span className="px-2.5 font-serif text-[26px] italic tracking-[-0.01em]">Minuta</span>}
      <button
        onClick={buscar}
        title="Buscar o hacer algo (Ctrl+K)"
        aria-label="Buscar o hacer algo"
        className={`flex items-center gap-2 rounded-lg border border-borde bg-fondo py-2 text-[13px] text-tenue hover:text-tinta ${chica ? "justify-center px-0" : "px-2.5"}`}
      >
        <Search size={14} aria-hidden />
        {!chica && (
          <>
            <span className="flex-1 text-left">Buscar…</span>
            <kbd className="rounded border border-linea px-1 font-mono text-[10.5px]">Ctrl K</kbd>
          </>
        )}
      </button>
      <ul className="flex flex-col gap-0.5">{ARRIBA.map(item)}</ul>
      <div className="mt-auto flex flex-col gap-0.5">
        <ul className="flex flex-col gap-0.5">{ABAJO.map(item)}</ul>
        <button
          onClick={alternar}
          title={chica ? "Agrandar la barra" : "Achicar la barra"}
          aria-label={chica ? "Agrandar la barra" : "Achicar la barra"}
          className={`flex items-center gap-2.5 rounded-md py-2 text-[12.5px] text-tenue hover:bg-seleccion/60 hover:text-tinta ${chica ? "justify-center" : "px-2.5"}`}
        >
          {chica ? <PanelLeftOpen size={15} aria-hidden /> : <PanelLeftClose size={15} aria-hidden />}
          {!chica && "Achicar"}
        </button>
        {motores && !chica && (
          <p className="mt-1 border-t border-linea px-2.5 pt-3 text-xs leading-relaxed text-tenue">
            {motores.configurados.assemblyai && !motores.assemblyai.agotado && <>AssemblyAI · quedan {usd(motores.assemblyai.credito_estimado_usd)}<br /></>}
            {motores.configurados.groq && <>Groq · {Math.round(motores.groq.hoy_s / 60)} de {motores.groq.cuota_diaria_s / 60} min hoy</>}
          </p>
        )}
      </div>
    </nav>
  );
}
