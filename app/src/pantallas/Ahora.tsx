import { Laptop, Pause, Play, Scissors, Star } from "lucide-react";
import { ATAJO_MARCA, ListaMarcas, marcarAhora } from "../componentes/Marcas";
import { api } from "../api/cliente";
import type { Estado } from "../api/tipos";
import { Alertas } from "../componentes/Alertas";
import { EnProceso } from "../componentes/EnProceso";
import { useAvisos } from "../componentes/Avisos";
import { Boton, Medidor, PuntoGrabando, Rotulo } from "../componentes/Basicos";
import { mmss, tituloProvisorio } from "../util/formato";
import { Ayuda } from "../componentes/Ayuda";
import { Anotar } from "../componentes/Anotar";
import { Brief } from "../componentes/Brief";
import { GrabarAMano } from "../componentes/GrabarAMano";

const AVISO = "Aviso: estoy grabando esta reunión para tomar notas. Si preferís que no, avisame y corto.";

export function Ahora({ estado, refrescar, abrir }: { estado: Estado | null; refrescar: () => void; abrir?: (id: string) => void }) {
  const { avisar, conDeshacer } = useAvisos();
  const a = estado?.ahora;

  const pausar = async (pausado: boolean) => {
    await api("/api/pausa", { metodo: "POST", cuerpo: { pausado } });
    refrescar();
    avisar(pausado ? "Minuta no va a grabar hasta que la reanudes" : "Minuta vuelve a grabar las reuniones");
  };

  const soloLocal = async (valor: boolean) => {
    await api("/api/grabacion-actual/solo-local", { metodo: "POST", cuerpo: { solo_local: valor } });
    refrescar();
    avisar(valor ? "Esta reunión se va a transcribir solo en esta PC" : "Esta reunión vuelve a la regla de su carpeta");
  };

  const marcar = async () => {
    const m = await marcarAhora();
    refrescar();
    avisar(`Marcado en ${mmss(m.t)}`);
  };

  const cortar = async () => {
    await api("/api/grabacion-actual/cortar", { metodo: "POST" });
    refrescar();
    avisar(a?.manual ? "Grabación terminada" : "Grabación cortada. Si la reunión sigue, la próxima empieza sola");
  };

  const noEsReunion = () =>
    conDeshacer("Se descarta esta grabación", async () => {
      await api("/api/grabacion-actual/descartar", { metodo: "POST" });
      refrescar();
    });

  return (
    <main className="flex flex-1 justify-center overflow-y-auto px-[clamp(24px,6vw,96px)] py-16">
      <div className="flex w-full max-w-[820px] flex-col gap-11">
        {a?.grabando ? (
          <>
            <Alertas alertas={a.alertas} alActuar={refrescar} />
            <section aria-label="Grabación en curso" className="flex flex-col gap-[18px]">
              <span className="flex items-center gap-2.5 text-xs font-semibold uppercase tracking-[0.1em] text-acento">
                <PuntoGrabando grande /> {a.soltado ? "Grabando · la llamada parece haber terminado" : "Grabando"}
              </span>
              <h1 className="m-0 font-serif text-[46px] font-medium leading-[1.08] tracking-[-0.02em]">
                {tituloProvisorio(a.ventanas, a.apps, a.titulo)}
              </h1>
              <p className="m-0 flex flex-wrap items-center gap-2 text-[13px] text-tenue">
                <Laptop size={14} aria-hidden />
                {a.solo_local
                  ? "El audio se graba en esta PC y se transcribe solo acá: no sale a ningún servicio."
                  : "El audio se graba en esta PC. Al terminar se transcribe según la regla de privacidad de su carpeta."}
                <button
                  onClick={() => soloLocal(!a.solo_local)}
                  className="rounded underline decoration-borde underline-offset-4 hover:text-tinta hover:decoration-tinta"
                >
                  {a.solo_local ? "Usar la regla de la carpeta" : "Solo esta vez, en esta PC"}
                </button>
              </p>
              <div className="font-serif text-[96px] leading-none tracking-[-0.03em] tabular-nums" aria-live="off">
                {mmss(a.segundos)}
              </div>
              <Anotar alAnotar={refrescar} />
            </section>
            {/* Reuniones seguidas: la próxima empieza mientras se graba esta (sin evento cercano no muestra nada). */}
            <Brief abrir={abrir} />
            {!!a.marcas?.length && (
              <section aria-label="Notas y marcas de esta reunión" className="flex flex-col gap-1">
                <Rotulo>Notas y marcas · se pueden etiquetar y editar ahora o después</Rotulo>
                <ListaMarcas marcas={[...a.marcas].reverse()} alCambiar={refrescar} />
              </section>
            )}
            <section aria-label="Niveles de audio" className="grid grid-cols-2 gap-7">
              <div className="flex flex-col gap-2.5">
                <div className="flex justify-between text-[13px]">
                  <span className="font-medium">Tu micrófono</span>
                  <span className="text-tenue">{a.dispositivos?.mic}</span>
                </div>
                <Medidor nivel={a.niveles?.mic ?? 0} etiqueta="Nivel de tu micrófono" />
              </div>
              <div className="flex flex-col gap-2.5">
                <div className="flex justify-between text-[13px]">
                  <span className="font-medium">Lo que escuchás</span>
                  <span className="text-tenue">{a.dispositivos?.salida}</span>
                </div>
                <Medidor nivel={a.niveles?.sistema ?? 0} etiqueta="Nivel del audio de la reunión" />
              </div>
            </section>
            <section aria-label="Acciones" className="flex flex-wrap items-center gap-3">
              <Ayuda texto="Marcar, también desde cualquier aplicación" atajo={ATAJO_MARCA}>
                <Boton variante="primario" className="min-h-11 px-5" onClick={marcar}>
                  <Star size={14} aria-hidden /> Marcar este momento
                </Boton>
              </Ayuda>
              <Boton className="min-h-11 px-[18px]" onClick={() => pausar(true)}>
                <Pause size={14} aria-hidden /> Pausar
              </Boton>
              <Ayuda texto={a.manual ? "Termina la grabación" : "Separa dos reuniones seguidas: la siguiente empieza sola"}>
                <Boton className="min-h-11 px-[18px]" onClick={cortar}>
                  <Scissors size={14} aria-hidden /> {a.manual ? "Terminar" : "Cortar acá"}
                </Boton>
              </Ayuda>
              <Boton className="min-h-11 px-[18px]" onClick={noEsReunion}>
                No es una reunión
              </Boton>
              <Boton
                className="min-h-11 px-[18px]"
                onClick={async () => {
                  await navigator.clipboard.writeText(AVISO);
                  avisar("Aviso copiado: pegalo en el chat de la reunión");
                }}
              >
                Copiar aviso de grabación
              </Boton>
              <span className="text-[13px] text-tenue">
                {a.manual
                  ? "Grabación a mano: sigue hasta que toques Terminar, o corta sola tras 10 minutos de silencio."
                  : "Cortá la llamada y en 90 segundos cierra sola."}
              </span>
            </section>
          </>
        ) : (
          <>
          <Brief abrir={abrir} />
          <section className="flex flex-col gap-5">
            <Rotulo>{a?.pausado ? "En pausa" : "En espera"}</Rotulo>
            <h1 className="m-0 font-serif text-[40px] font-medium leading-tight tracking-[-0.02em]">
              {a?.pausado ? "Minuta no está grabando." : "No hay ninguna reunión en curso."}
            </h1>
            <p className="m-0 max-w-[56ch] text-[15px] leading-relaxed text-tenue">
              {a?.pausado
                ? "Las reuniones de este rato no se van a grabar. Reanudá cuando quieras."
                : "Minuta empieza a grabar sola cuando Chrome, Edge o Slack toman el micrófono, y te avisa. Para una reunión presencial, grabala a mano."}
            </p>
            {!a?.pausado && a?.sin_deteccion && (
              <p role="alert" className="m-0 max-w-[56ch] text-[15px] font-medium leading-relaxed text-acento">
                Ahora no puedo ver qué app usa el micrófono, así que las reuniones no se graban solas. Grabalas a mano.
              </p>
            )}
            {a?.pausado ? (
              <Boton variante="primario" className="min-h-11 w-fit px-5" onClick={() => pausar(false)}>
                <Play size={14} fill="currentColor" aria-hidden /> Reanudar
              </Boton>
            ) : (
              <Boton className="min-h-11 w-fit px-5" onClick={() => pausar(true)}>
                <Pause size={14} aria-hidden /> Pausar las grabaciones
              </Boton>
            )}
          </section>
          <section aria-label="Reunión presencial" className="flex flex-col gap-3">
            <Rotulo>Reunión presencial</Rotulo>
            <GrabarAMano
              alEmpezar={(mensaje) => {
                refrescar();
                avisar(mensaje);
              }}
            />
          </section>
          </>
        )}

        {!!(estado?.cola.length || estado?.errores?.length) && (
          <section className="flex flex-col gap-3.5 border-t border-linea pt-6">
            <h2 className="m-0 text-xs font-medium uppercase tracking-[0.08em] text-tenue">En proceso</h2>
            <EnProceso estado={estado} alCambiar={refrescar} compacto />
          </section>
        )}
      </div>
    </main>
  );
}
