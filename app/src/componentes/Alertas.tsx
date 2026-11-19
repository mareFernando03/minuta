// Alertas de la grabación en curso (las decide el vigía del motor): micrófono mudo o sin señal, cambio de
// dispositivo y silencio prolongado. Se muestran en «Ahora» y, compactas, en el panel de la bandeja.

import { useEffect, useState } from "react";
import { MicOff, Volume2 } from "lucide-react";
import { api, enTauri } from "../api/cliente";
import type { Alerta } from "../api/tipos";
import { Boton } from "./Basicos";

async function abrirSonidoDeWindows() {
  if (!enTauri()) return;
  const { openUrl } = await import("@tauri-apps/plugin-opener");
  await openUrl("ms-settings:sound");
}

/** Segundos que faltan hasta `vence` (ISO), actualizados cada segundo. */
function useCuentaRegresiva(vence: string | null) {
  const [faltan, setFaltan] = useState<number | null>(null);
  useEffect(() => {
    if (!vence) return setFaltan(null);
    const calcular = () => setFaltan(Math.max(0, Math.round((new Date(vence).getTime() - Date.now()) / 1000)));
    calcular();
    const t = window.setInterval(calcular, 1000);
    return () => window.clearInterval(t);
  }, [vence]);
  return faltan;
}

function UnaAlerta({ a, compacta, alActuar }: { a: Alerta; compacta: boolean; alActuar: () => void }) {
  const faltan = useCuentaRegresiva(a.vence);
  const accion = async (ruta: string) => {
    await api(ruta, { metodo: "POST" });
    alActuar();
  };
  const deAudio = a.tipo === "mic_mudo" || a.tipo === "sin_senal" || a.tipo === "sistema_mudo" || a.tipo === "dispositivo";
  return (
    <div
      role={a.tipo === "dispositivo" ? "status" : "alert"}
      className={`flex flex-col gap-2.5 rounded-[10px] border border-acento bg-acento-suave ${compacta ? "p-3" : "p-4"}`}
    >
      <p className={`m-0 flex items-start gap-2 leading-snug ${compacta ? "text-[13px]" : "text-[14.5px]"}`}>
        {a.tipo === "silencio" || a.tipo === "sistema_mudo" ? <Volume2 size={16} className="mt-0.5 shrink-0 text-acento" aria-hidden /> : <MicOff size={16} className="mt-0.5 shrink-0 text-acento" aria-hidden />}
        <span>
          {a.mensaje}
          {a.tipo === "silencio" && faltan !== null && (
            <span className="text-tenue"> Si no, corta en {Math.floor(faltan / 60)}:{String(faltan % 60).padStart(2, "0")}.</span>
          )}
        </span>
      </p>
      <div className="flex flex-wrap gap-2">
        {a.tipo === "silencio" && (
          <>
            <Boton variante="primario" onClick={() => accion("/api/grabacion-actual/seguir")}>
              Sigue la reunión
            </Boton>
            <Boton onClick={() => accion("/api/grabacion-actual/cortar")}>Terminó, cortar ahora</Boton>
          </>
        )}
        {deAudio && enTauri() && <Boton onClick={abrirSonidoDeWindows}>Abrir el sonido de Windows</Boton>}
      </div>
    </div>
  );
}

export function Alertas({ alertas = [], compacta = false, alActuar }: { alertas?: Alerta[]; compacta?: boolean; alActuar: () => void }) {
  if (!alertas.length) return null;
  return (
    <div className="flex flex-col gap-2.5">
      {alertas.map((a) => (
        <UnaAlerta key={a.tipo} a={a} compacta={compacta} alActuar={alActuar} />
      ))}
    </div>
  );
}
