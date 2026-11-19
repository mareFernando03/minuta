import { useEffect, useState } from "react";
import { api, useDatos } from "../api/cliente";
import type { Glosario as G } from "../api/tipos";
import { useAvisos } from "../componentes/Avisos";
import { Boton, Cargando, ErrorCaja } from "../componentes/Basicos";

export function Glosario() {
  const { datos, error, cargando, recargar, setDatos } = useDatos<G>("/api/glosario");
  const { avisar } = useAvisos();
  const [prompt, setPrompt] = useState("");
  const [terminos, setTerminos] = useState("");

  useEffect(() => {
    if (datos) {
      setPrompt(datos.prompt);
      setTerminos(datos.terminos);
    }
  }, [datos]);

  if (error) return <ErrorCaja mensaje={error} reintentar={recargar} />;
  if (cargando && !datos) return <Cargando />;
  if (!datos) return null;
  const cambiado = prompt !== datos.prompt || terminos !== datos.terminos;
  const pasado = datos.tokens_prompt != null && datos.tokens_prompt > datos.limite_tokens;

  const guardar = async () => {
    const g = await api<G>("/api/glosario", { metodo: "PUT", cuerpo: { prompt, terminos } });
    setDatos(g);
    avisar(g.tokens_prompt != null && g.tokens_prompt > g.limite_tokens ? "Guardado, pero el prompt se pasa del límite: Whisper ignora el principio" : "Glosario guardado");
  };

  return (
    <main className="flex-1 overflow-y-auto px-[clamp(24px,6vw,96px)] py-12">
      <div className="mx-auto flex max-w-[820px] flex-col gap-8">
        <header className="flex items-end justify-between gap-4">
          <div className="flex flex-col gap-2">
            <h1 className="m-0 font-serif text-[34px] font-medium tracking-[-0.015em]">Glosario</h1>
            <p className="m-0 max-w-[62ch] text-[14px] leading-relaxed text-tenue">
              Nombres y términos que la transcripción tiene que escribir bien. Cuando veas uno mal en una reunión, sumalo acá: se aplica desde la
              próxima.
            </p>
          </div>
          <Boton variante="primario" disabled={!cambiado} onClick={guardar}>
            Guardar
          </Boton>
        </header>

        <label className="flex flex-col gap-2">
          <span className="flex items-baseline justify-between">
            <span className="font-medium">Lo que escucha Whisper</span>
            <span className={`font-mono text-xs ${pasado ? "font-semibold text-acento" : "text-tenue"}`}>
              {datos.tokens_prompt ?? "?"} / {datos.limite_tokens} tokens{cambiado ? " · guardá para recontar" : ""}
            </span>
          </span>
          <span className="text-[13px] text-tenue">Una frase corta con los nombres más frecuentes. Si se pasa del límite, Whisper descarta el principio.</span>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} className="rounded-lg border border-borde bg-fondo p-3 font-serif text-[16px] leading-relaxed outline-none" />
        </label>

        <label className="flex flex-col gap-2">
          <span className="font-medium">Términos para corregir después</span>
          <span className="text-[13px] text-tenue">
            Uno por línea. Después de «&lt;-» van las deformaciones ya vistas, por ejemplo: <code className="font-mono">Contoso &lt;- consumidor, Sontoso</code>
          </span>
          <textarea value={terminos} onChange={(e) => setTerminos(e.target.value)} rows={18} spellCheck={false} className="rounded-lg border border-borde bg-fondo p-3 font-mono text-[13px] leading-relaxed outline-none" />
        </label>
      </div>
    </main>
  );
}
