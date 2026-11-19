import { useDatos } from "../api/cliente";
import type { Motores as M, Trabajo } from "../api/tipos";
import { Cargando, ErrorCaja, Rotulo } from "../componentes/Basicos";
import { usd } from "../util/formato";

function Barra({ valor, maximo, etiqueta }: { valor: number; maximo: number; etiqueta: string }) {
  const pct = maximo ? Math.min(100, (valor / maximo) * 100) : 0;
  return (
    <div role="meter" aria-label={etiqueta} aria-valuemin={0} aria-valuemax={maximo} aria-valuenow={valor} className="h-1.5 overflow-hidden rounded-full bg-linea">
      <div className="h-full rounded-full bg-tinta" style={{ width: `${pct}%` }} />
    </div>
  );
}

function Fila({ nombre, estado, bien, children }: { nombre: string; estado: string; bien: boolean; children?: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-linea py-5">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="m-0 font-serif text-[22px] font-medium">{nombre}</h2>
        <span className={`flex items-center gap-2 text-[13px] ${bien ? "text-tenue" : "font-medium text-acento"}`}>
          <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${bien ? "bg-ok" : "bg-acento"}`} />
          {estado}
        </span>
      </div>
      {children}
    </section>
  );
}

export function Motores() {
  const { datos: m, error, cargando, recargar } = useDatos<M>("/api/motores");
  const { datos: trabajos } = useDatos<Trabajo[]>("/api/trabajos");
  if (error) return <ErrorCaja mensaje={error} reintentar={recargar} />;
  if (cargando && !m) return <Cargando />;
  if (!m) return null;
  const errores = (trabajos ?? []).filter((t) => t.error).slice(0, 8);

  return (
    <main className="flex-1 overflow-y-auto px-[clamp(24px,6vw,96px)] py-12">
      <div className="mx-auto flex max-w-[760px] flex-col">
        <h1 className="m-0 mb-2 font-serif text-[34px] font-medium tracking-[-0.015em]">Motores</h1>
        <p className="m-0 mb-6 max-w-[62ch] text-[14px] leading-relaxed text-tenue">
          Las reuniones de trabajo van a AssemblyAI mientras dure el crédito; el resto, y todo cuando se termine, a Groq. Si no hay red o cuota, se
          transcriben en esta PC. Lo que no se identifica como reunión nunca sale de la notebook.
        </p>

        <Fila
          nombre="AssemblyAI"
          bien={m.configurados.assemblyai && !m.assemblyai.agotado}
          estado={!m.configurados.assemblyai ? "Sin clave" : m.assemblyai.agotado ? "Crédito agotado: todo va a Groq" : "Disponible"}
        >
          <Barra valor={m.assemblyai.gastado_usd} maximo={50} etiqueta="Crédito de AssemblyAI usado" />
          <p className="m-0 text-[13px] text-tenue">
            Usado {usd(m.assemblyai.gastado_usd)} de US$50 · quedan unos {usd(Math.max(0, m.assemblyai.credito_estimado_usd))}. Es una estimación: AssemblyAI no
            informa el saldo por su API.
          </p>
        </Fila>

        <Fila nombre="Groq" bien={m.configurados.groq} estado={m.configurados.groq ? "Disponible · gratis" : "Sin clave"}>
          <Barra valor={m.groq.hoy_s} maximo={m.groq.cuota_diaria_s} etiqueta="Cuota diaria de Groq usada" />
          <p className="m-0 text-[13px] text-tenue">
            Hoy {Math.round(m.groq.hoy_s / 60)} de {m.groq.cuota_diaria_s / 60} minutos de voz.
          </p>
        </Fila>

        <Fila nombre="Whisper en esta PC" bien estado={m.local.enchufada ? "Listo" : "Espera a que enchufes la notebook"}>
          <p className="m-0 text-[13px] text-tenue">
            Respaldo cuando la nube no está. Solo corre enchufada: a batería tarda el triple. {Math.round(m.local.segundos / 60)} minutos transcriptos acá.
          </p>
        </Fila>

        <Fila nombre="Corrección con el glosario" bien estado="Claude Haiku">
          <p className="m-0 text-[13px] text-tenue">Usó el equivalente a {usd(m.haiku.gastado_usd)}, dentro de tu suscripción de Claude.</p>
        </Fila>

        {errores.length > 0 && (
          <section className="flex flex-col gap-3 border-t border-linea pt-5">
            <Rotulo acento>Problemas recientes</Rotulo>
            <ul className="m-0 flex list-none flex-col gap-2 p-0 text-[13.5px]">
              {errores.map((t) => (
                <li key={t.id} className="flex justify-between gap-4">
                  <span>{t.error}</span>
                  <span className="shrink-0 text-tenue">{t.estado === "error" ? "sin solución automática" : "se reintenta solo"}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </main>
  );
}
