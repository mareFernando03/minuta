import { useEffect, useState } from "react";
import { api, enTauri, useDatos } from "../api/cliente";
import type { Ajustes as A } from "../api/tipos";
import { useAvisos } from "../componentes/Avisos";
import { Boton, Cargando, ErrorCaja, Rotulo } from "../componentes/Basicos";
import { configurarNotificaciones } from "../util/notificar";

const PRESETS: { id: string; nombre: string; motores: string[] }[] = [
  { id: "mejor", nombre: "AssemblyAI → Groq → esta PC", motores: ["assemblyai", "groq", "local"] },
  { id: "gratis", nombre: "Groq → esta PC", motores: ["groq", "local"] },
  { id: "local", nombre: "Solo en esta PC", motores: ["local"] },
];

const presetDe = (m: string[] = []) => PRESETS.find((p) => p.motores.join() === m.join())?.id ?? "mejor";

export function Ajustes() {
  const { datos: a, error, cargando, recargar, setDatos } = useDatos<A>("/api/ajustes");
  const { avisar } = useAvisos();
  if (error) return <ErrorCaja mensaje={error} reintentar={recargar} />;
  if (cargando && !a) return <Cargando />;
  if (!a) return null;

  const guardar = async (cambios: Partial<A>) => {
    try {
      setDatos(await api<A>("/api/ajustes", { metodo: "PUT", cuerpo: cambios }));
      avisar("Guardado");
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };

  return (
    <main className="flex-1 overflow-y-auto px-[clamp(24px,6vw,96px)] py-12">
      <div className="mx-auto flex max-w-[760px] flex-col gap-10">
        <h1 className="m-0 font-serif text-[34px] font-medium tracking-[-0.015em]">Ajustes</h1>

        <section className="flex flex-col gap-4">
          <Rotulo>Privacidad por carpeta</Rotulo>
          <p className="m-0 text-[13.5px] leading-relaxed text-tenue">
            Dónde se transcribe cada tipo de reunión. Lo que no se identifica como reunión se transcribe siempre en esta PC y no se puede cambiar.
          </p>
          <div className="flex flex-col">
            {[...a.carpetas, "*"].map((c) => (
              <label key={c} className="flex items-center justify-between gap-4 border-t border-linea py-3">
                <span>{c === "*" ? "Otras reuniones identificadas" : c}</span>
                <select
                  value={presetDe(a.privacidad[c] ?? a.privacidad["*"])}
                  onChange={(e) => guardar({ privacidad: { ...a.privacidad, [c]: PRESETS.find((p) => p.id === e.target.value)!.motores } })}
                  className="rounded-lg border border-borde bg-fondo px-3 py-2"
                >
                  {PRESETS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.nombre}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-4">
          <Rotulo>Audio y exportación</Rotulo>
          <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
            <span>
              Guardar el audio
              <span className="block text-[12.5px] text-tenue">Para reescuchar o reprocesar. La transcripción queda siempre.</span>
            </span>
            <select value={a.audio_dias} onChange={(e) => guardar({ audio_dias: Number(e.target.value) })} className="rounded-lg border border-borde bg-fondo px-3 py-2">
              {[0, 7, 14, 30, 90].map((d) => (
                <option key={d} value={d}>
                  {d === 0 ? "No guardarlo" : `${d} días`}
                </option>
              ))}
            </select>
          </label>
          <CampoTexto
            etiqueta="Exportar las reuniones a"
            ayuda="Carpeta con JSON en formato Granola, la que leen los corpus de reuniones."
            valor={a.exportar_a}
            alGuardar={(v) => guardar({ exportar_a: v })}
          />
        </section>

        <section className="flex flex-col gap-4">
          <Rotulo>Dictado</Rotulo>
          <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
            <span>
              Dictar con Ctrl+Win sostenido
              <span className="block text-[12.5px] text-tenue">
                Mientras lo mantenés, Minuta escucha el micrófono; al soltar, pega el texto donde está el cursor. El audio no se guarda.
              </span>
            </span>
            <input type="checkbox" checked={a.dictado} onChange={(e) => guardar({ dictado: e.target.checked })} className="size-4 accent-[var(--color-tinta)]" />
          </label>
          <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
            <span>
              Dónde se transcribe
              <span className="block text-[12.5px] text-tenue">
                Con Groq el audio sale de esta PC y vuelve en menos de un segundo; sin red cae a esta PC, que tarda más.
              </span>
            </span>
            <select
              value={a.dictado_motor}
              onChange={(e) => guardar({ dictado_motor: e.target.value as A["dictado_motor"] })}
              className="rounded-lg border border-borde bg-fondo px-3 py-2"
            >
              <option value="groq">Groq → esta PC</option>
              <option value="local">Solo en esta PC</option>
            </select>
          </label>
        </section>

        {enTauri() && (
          <EstaPC
            notificaciones={a.notificaciones}
            alCambiarNotificaciones={(v) => {
              configurarNotificaciones(v);
              guardar({ notificaciones: v });
            }}
          />
        )}

        <section className="flex flex-col gap-4">
          <Rotulo>Quién habla dentro de «Ellos»</Rotulo>
          <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
            <span>
              Separar las voces de los demás
              <span className="block text-[12.5px] text-tenue">En esta PC y solo enchufada, después de transcribir. Una hora de reunión lleva unos 8 minutos.</span>
            </span>
            <input type="checkbox" checked={a.diarizar} onChange={(e) => guardar({ diarizar: e.target.checked })} className="size-4 accent-[var(--color-tinta)]" />
          </label>
          <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
            <span>
              Poner nombres solos cuando la voz es inequívoca
              <span className="block text-[12.5px] text-tenue">
                Apagado, Minuta solo sugiere «¿Es Pedro?». Conviene activarlo después de medir con reuniones reales de varias personas.
              </span>
            </span>
            <input type="checkbox" checked={a.diarizacion_auto} onChange={(e) => guardar({ diarizacion_auto: e.target.checked })} className="size-4 accent-[var(--color-tinta)]" />
          </label>
          <Personas />
        </section>

        <section className="flex flex-col gap-4">
          <Rotulo>Resumen con Claude</Rotulo>
          <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
            <span>
              Resumir cada reunión al terminar
              <span className="block text-[12.5px] text-tenue">
                Con la suscripción de Claude Code. Solo las reuniones cuya carpeta permite la nube: lo que se transcribe solo en esta PC tampoco se resume.
              </span>
            </span>
            <input type="checkbox" checked={a.resumir} onChange={(e) => guardar({ resumir: e.target.checked })} className="size-4 accent-[var(--color-tinta)]" />
          </label>
          <div className="flex flex-col">
            <span className="border-t border-linea pt-3 text-[12.5px] text-tenue">
              Plantilla por carpeta: las secciones que querés en el resumen, una por línea. Vacía, Claude elige según la reunión.
            </span>
            {a.carpetas.map((c) => (
              <Plantilla key={c} carpeta={c} valor={a.plantillas[c] ?? ""} alGuardar={(v) => guardar({ plantillas: { ...a.plantillas, [c]: v } })} />
            ))}
          </div>
        </section>

        <Secretos secretos={a.secretos} alGuardar={recargar} />
      </div>
    </main>
  );
}

function Plantilla({ carpeta, valor, alGuardar }: { carpeta: string; valor: string; alGuardar: (v: string) => void }) {
  const [texto, setTexto] = useState(valor);
  useEffect(() => setTexto(valor), [valor]);
  return (
    <details className="border-b border-linea py-2.5">
      <summary className="cursor-pointer">
        {carpeta} <span className="text-[12.5px] text-tenue">· {valor.trim() ? `${valor.trim().split("\n").length} secciones` : "Auto"}</span>
      </summary>
      <textarea
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        onBlur={() => texto !== valor && alGuardar(texto)}
        maxLength={4000}
        rows={5}
        placeholder={"Contexto\nDecisiones\nRiesgos\nPróximos pasos"}
        aria-label={`Plantilla del resumen para ${carpeta}`}
        className="mt-2 w-full rounded-lg border border-borde bg-fondo px-3 py-2 font-mono text-[13px] outline-none focus:border-tinta"
      />
    </details>
  );
}

/** Las personas con huella de voz. Olvidar una borra su huella y su nombre en todas las reuniones. */
function Personas() {
  const { datos, recargar } = useDatos<{ id: number; nombre: string; reuniones: number }[]>("/api/voces");
  const { conDeshacer } = useAvisos();
  const [ocultas, setOcultas] = useState<Set<number>>(new Set());
  const lista = (datos ?? []).filter((p) => !ocultas.has(p.id));
  if (!lista.length) return null;
  return (
    <div className="flex flex-col">
      <span className="border-t border-linea pt-3 text-[12.5px] text-tenue">Voces que Minuta reconoce. La huella se guarda solo en esta PC.</span>
      <ul className="m-0 list-none p-0">
        {lista.map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-4 py-2">
            <span>
              {p.nombre} <span className="text-[12.5px] text-tenue">· {p.reuniones === 1 ? "1 reunión" : `${p.reuniones} reuniones`}</span>
            </span>
            <Boton
              variante="fantasma"
              onClick={() => {
                setOcultas((o) => new Set(o).add(p.id));
                conDeshacer(
                  `Se olvidó la voz de ${p.nombre}`,
                  () => api(`/api/voces/${p.id}`, { metodo: "DELETE" }).then(recargar),
                  () => setOcultas((o) => new Set([...o].filter((x) => x !== p.id))),
                );
              }}
            >
              Olvidar
            </Boton>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Lo que depende de la app de escritorio y no del motor: inicio con Windows y atajo global. */
function EstaPC({ notificaciones, alCambiarNotificaciones }: { notificaciones: boolean; alCambiarNotificaciones: (v: boolean) => void }) {
  const [inicio, setInicio] = useState<boolean | null>(null);
  const { avisar } = useAvisos();
  useEffect(() => {
    import("@tauri-apps/plugin-autostart").then((m) => m.isEnabled().then(setInicio)).catch(() => setInicio(null));
  }, []);
  const cambiar = async (activar: boolean) => {
    try {
      const m = await import("@tauri-apps/plugin-autostart");
      await (activar ? m.enable() : m.disable());
      setInicio(await m.isEnabled());
      avisar(activar ? "Minuta arranca con Windows" : "Minuta ya no arranca con Windows");
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };
  return (
    <section className="flex flex-col gap-4">
      <Rotulo>Esta PC</Rotulo>
      <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
        <span>
          Notificaciones de Windows
          <span className="block text-[12.5px] text-tenue">
            Al empezar a grabar, si el micrófono no capta nada, al marcar con el atajo y cuando una reunión está lista. No salen si Minuta está en primer plano.
          </span>
        </span>
        <input type="checkbox" checked={notificaciones} onChange={(e) => alCambiarNotificaciones(e.target.checked)} className="size-4 accent-[var(--color-tinta)]" />
      </label>
      <label className="flex items-center justify-between gap-4 border-t border-linea py-3">
        <span>
          Iniciar con Windows
          <span className="block text-[12.5px] text-tenue">Arranca en la bandeja, sin abrir la ventana. Si no arranca, no graba.</span>
        </span>
        <input
          type="checkbox"
          checked={!!inicio}
          disabled={inicio === null}
          onChange={(e) => cambiar(e.target.checked)}
          className="size-4 accent-[var(--color-tinta)]"
        />
      </label>
      <div className="flex items-center justify-between gap-4 border-t border-linea py-3">
        <span>
          Pausar o reanudar desde cualquier aplicación
          <span className="block text-[12.5px] text-tenue">Atajo global. También desde el menú del ícono de la bandeja.</span>
        </span>
        <kbd className="rounded border border-borde px-2 py-1 font-mono text-[12px]">Ctrl+Alt+Shift+P</kbd>
      </div>
    </section>
  );
}

function CampoTexto({ etiqueta, ayuda, valor, alGuardar }: { etiqueta: string; ayuda: string; valor: string; alGuardar: (v: string) => void }) {
  const [v, setV] = useState(valor);
  return (
    <label className="flex flex-col gap-1.5 border-t border-linea py-3">
      <span>{etiqueta}</span>
      <span className="text-[12.5px] text-tenue">{ayuda}</span>
      <span className="flex gap-2">
        <input value={v} onChange={(e) => setV(e.target.value)} className="flex-1 rounded-lg border border-borde bg-fondo px-3 py-2 font-mono text-[13px] outline-none" />
        <Boton disabled={v === valor} onClick={() => alGuardar(v)}>
          Guardar
        </Boton>
      </span>
    </label>
  );
}

function Secretos({ secretos, alGuardar }: { secretos: A["secretos"]; alGuardar: () => void }) {
  const { avisar } = useAvisos();
  const [nuevo, setNuevo] = useState({ nombre: "", url: "" });

  const enviar = async (cambios: Record<string, unknown>, mensaje: string) => {
    try {
      await api("/api/secretos", { metodo: "PUT", cuerpo: cambios });
      avisar(mensaje);
      alGuardar();
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };

  return (
    <section className="flex flex-col gap-4">
      <Rotulo>Claves y calendarios</Rotulo>
      <p className="m-0 text-[13.5px] leading-relaxed text-tenue">
        Se guardan en el Administrador de credenciales de Windows. Minuta nunca las vuelve a mostrar: para cambiar una, escribila de nuevo.
      </p>
      {(["assemblyai", "groq"] as const).map((n) => (
        <Clave key={n} nombre={n === "assemblyai" ? "AssemblyAI" : "Groq"} configurada={secretos[n]} alGuardar={(v) => enviar({ [n]: v }, v ? "Clave guardada" : "Clave borrada")} />
      ))}
      <div className="flex flex-col gap-2 border-t border-linea py-3">
        <span>Calendarios</span>
        <span className="text-[12.5px] text-tenue">URL privada iCal. De ahí salen el título, los participantes y la carpeta de cada reunión.</span>
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {secretos.calendarios.map((c) => (
            <li key={c} className="flex items-center justify-between rounded-md bg-papel px-3 py-2 text-[13.5px]">
              <span>{c}</span>
              <button onClick={() => enviar({ calendarios: { [c]: null } }, `Se quitó «${c}»`)} className="text-tenue underline underline-offset-4 hover:text-tinta">
                Quitar
              </button>
            </li>
          ))}
        </ul>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            enviar({ calendarios: { [nuevo.nombre.trim()]: nuevo.url.trim() } }, "Calendario guardado");
            setNuevo({ nombre: "", url: "" });
          }}
        >
          <input required placeholder="Nombre" aria-label="Nombre del calendario" value={nuevo.nombre} onChange={(e) => setNuevo({ ...nuevo, nombre: e.target.value })} className="w-40 rounded-lg border border-borde bg-fondo px-3 py-2 outline-none" />
          <input required type="url" placeholder="https://…/basic.ics" aria-label="URL privada iCal" value={nuevo.url} onChange={(e) => setNuevo({ ...nuevo, url: e.target.value })} className="min-w-60 flex-1 rounded-lg border border-borde bg-fondo px-3 py-2 font-mono text-[13px] outline-none" />
          <Boton type="submit">Agregar</Boton>
        </form>
      </div>
    </section>
  );
}

function Clave({ nombre, configurada, alGuardar }: { nombre: string; configurada: boolean; alGuardar: (v: string) => void }) {
  const [v, setV] = useState("");
  return (
    <form
      className="flex flex-col gap-1.5 border-t border-linea py-3"
      onSubmit={(e) => {
        e.preventDefault();
        alGuardar(v.trim());
        setV("");
      }}
    >
      <span className="flex justify-between">
        <span>{nombre}</span>
        <span className={`text-[13px] ${configurada ? "text-tenue" : "font-medium text-acento"}`}>{configurada ? "Configurada" : "Sin configurar"}</span>
      </span>
      <span className="flex gap-2">
        <input type="password" autoComplete="off" placeholder={configurada ? "Escribí una nueva para reemplazarla" : "Pegá la clave"} aria-label={`Clave de ${nombre}`} value={v} onChange={(e) => setV(e.target.value)} className="flex-1 rounded-lg border border-borde bg-fondo px-3 py-2 font-mono text-[13px] outline-none" />
        <Boton type="submit" disabled={!v.trim()}>
          Guardar
        </Boton>
      </span>
    </form>
  );
}
