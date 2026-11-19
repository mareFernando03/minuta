// Transcripción sincronizada con el audio.
// Reglas (docs/plan-v1.md): la línea que suena se marca con raya, fondo y «Sonando» (no solo color); si la persona
// hace scroll se pausa el seguimiento y aparece «Volver a lo que suena»; los tiempos son botones; Espacio, J y L.
// Lectura fina (TASK-18): la palabra que suena se resalta, un clic en una palabra lleva el audio ahí, las palabras
// dudosas van subrayadas (] y [ para ir de una a otra), al reanudar tras una pausa larga retrocede 2 s y se pueden
// saltear los silencios. Cada turno es un componente memoizado (Turno.tsx): avanzar el audio no redibuja todos.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, urlAudio } from "../../api/cliente";
import type { Marca, Palabra, Reunion, Segmento } from "../../api/tipos";
import { useAvisos } from "../../componentes/Avisos";
import { ListaMarcas } from "../../componentes/Marcas";
import { Rotulo } from "../../componentes/Basicos";
import { mmss } from "../../util/formato";
import { BarraReunion } from "./BarraReunion";
import { BarraSeleccion } from "./BarraSeleccion";
import { Buscador } from "./Buscador";
import type { Coincidencia } from "./buscar";
import { enlaceMarkdown, lineaCopiable, seleccionComoTexto } from "./copiar";
import { pintar, palabraEnPunto, palabrasUtiles, posiciones, rango } from "./resaltado";
import { Reproductor } from "./Reproductor";
import { Turno, type AccionesTurno } from "./Turno";

// Medido con una reunión real (10/10): con el modelo small, menos de 0,5 marcaba el 27 % de las palabras, casi
// todas cortas y bien escritas («lo», «la», «de»). Menos de 0,25 y 4 letras o más deja el 4,5 % y ahí están los
// errores de verdad («admitor», «albino», «caraya»).
const DUDOSA = 0.25;
const esDudosa = (p: Palabra) => p[3] != null && p[3] < DUDOSA && p[0].replace(/[^\p{L}\p{N}]/gu, "").length >= 4;
const PAUSA_LARGA_MS = 5000;
const SILENCIO_LARGO_S = 2;

/** Pedido de ir a un minuto (resultado de búsqueda o enlace minuta://). `n` lo hace único: el mismo minuto dos
 * veces vuelve a saltar. Un enlace externo posiciona sin reproducir. */
export interface Salto {
  t: number;
  n: number;
  reproducir: boolean;
}

/** La etiqueta libre para «Una voz nueva»: la siguiente a la más alta en uso, contando las voces sin ficha. */
function vozNueva(r: Reunion) {
  const usadas = [...(r.voces ?? []).map((v) => v.etiqueta), ...r.segmentos.map((s) => s.voz ?? "")];
  return `V${Math.max(0, ...usadas.map((e) => Number(e.slice(1)) || 0)) + 1}`;
}

function leerPreferencia(clave: string) {
  try {
    return localStorage.getItem(clave) === "1";
  } catch {
    return false;
  }
}

function guardarPreferencia(clave: string, valor: boolean) {
  try {
    localStorage.setItem(clave, valor ? "1" : "0");
  } catch {
    /* sin almacenamiento: la preferencia dura la sesión */
  }
}

export function Transcripcion({ r, nombre, saltarA, alCambiar }: { r: Reunion; nombre: (s: Segmento) => string; saltarA?: Salto | null; alCambiar: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [versionAudio, setVersionAudio] = useState(0);
  const retomarEn = useRef<number | null>(null);
  const [t, setT] = useState(0);
  // La duración se conoce por la reunión: se muestra enseguida, sin esperar a que el audio lea sus metadatos.
  const [dur, setDur] = useState(() => [...r.segmentos].reverse().find((s) => s.fin != null)?.fin ?? 0);
  const [sonando, setSonando] = useState(false);
  const [vel, setVel] = useState(1);
  const [seguir, setSeguir] = useState(true);
  const [saltear, setSaltear] = useState(() => leerPreferencia("minuta.saltear"));
  const [buscando, setBuscando] = useState(false);
  const [halladas, setHalladas] = useState<Coincidencia[]>([]);
  const [editando, setEditando] = useState<number | null>(null); // id del turno, no su posición
  const [ocultos, setOcultos] = useState<Set<number>>(new Set());
  const { avisar, conAcciones, conDeshacer } = useAvisos();
  const lineas = useRef<(HTMLDivElement | null)[]>([]);
  const parrafos = useRef<(HTMLParagraphElement | null)[]>([]);
  const scrollPropio = useRef(false);
  const pausadoDesde = useRef<number | null>(null);
  const saltoExplicito = useRef(false);
  const saltoHecho = useRef<Salto | null>(null);
  const idReunion = encodeURIComponent(r.id);

  useEffect(() => {
    setSrc(null);
    if (r.audio && r.grabacion_id) urlAudio(r.grabacion_id).then((u) => setSrc(versionAudio ? `${u}&v=${versionAudio}` : u));
  }, [r.audio, r.grabacion_id, versionAudio]);

  const activo = useMemo(() => {
    let i = -1;
    r.segmentos.forEach((s, k) => {
      if (s.inicio != null && s.inicio <= t + 0.25) i = k;
    });
    return i;
  }, [r.segmentos, t]);

  // Cada marca se muestra en el margen del turno que estaba sonando cuando se marcó.
  const marcasPorTurno = useMemo(() => {
    const porTurno = new Map<number, Marca[]>();
    for (const m of r.marcas ?? []) {
      let i = 0;
      r.segmentos.forEach((s, k) => {
        if (s.inicio != null && s.inicio <= m.t) i = k;
      });
      porTurno.set(i, [...(porTurno.get(i) ?? []), m]);
    }
    return porTurno;
  }, [r.marcas, r.segmentos]);

  const utiles = useMemo(() => r.segmentos.map((s) => palabrasUtiles(s)), [r.segmentos]);

  // Palabras dudosas de toda la reunión, por tiempo (con turnos superpuestos, el orden de los turnos no alcanza).
  const dudosas = useMemo(() => {
    const lista: [number, number, number][] = [];
    utiles.forEach((palabras, i) =>
      palabras?.forEach((p, k) => {
        if (esDudosa(p)) lista.push([i, k, p[1]]);
      }),
    );
    return lista.sort((x, y) => x[2] - y[2]);
  }, [utiles]);

  const ir = useCallback((s: number, reproducir = true) => {
    const a = audio.current;
    if (!a) return;
    saltoExplicito.current = true;
    a.currentTime = Math.max(0, s);
    setT(s);
    setSeguir(true);
    if (reproducir) a.play().catch(() => setSonando(false));
  }, []);

  // Un pedido de salto se cumple una sola vez (un audio que se recarga no vuelve a saltar).
  useEffect(() => {
    if (!saltarA || saltoHecho.current === saltarA) return;
    if (src) {
      saltoHecho.current = saltarA;
      ir(saltarA.t, saltarA.reproducir);
    } else if (!r.audio) {
      // Sin audio (venció la retención): la cita igual lleva al turno que la respalda.
      saltoHecho.current = saltarA;
      const i = r.segmentos.reduce((k, s, j) => (s.inicio != null && s.inicio <= saltarA.t ? j : k), 0);
      lineas.current[i]?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [saltarA, src, ir, r.audio, r.segmentos]);

  // Subrayado de las dudosas. Se repinta cuando cambian los nodos de texto (editar, ocultar, deshacer).
  useEffect(() => {
    pintar(
      "dudosa",
      dudosas.map(([i, k]) => {
        const [a, b] = posiciones(r.segmentos[i].texto)[k];
        return rango(parrafos.current[i], a, b);
      }),
    );
    return () => pintar("dudosa", []);
  }, [dudosas, r.segmentos, editando, ocultos]);

  // La palabra que suena.
  const empezo = sonando || t > 0;
  useEffect(() => {
    const s = r.segmentos[activo];
    const palabras = utiles[activo];
    if (!s || !palabras || !empezo) return pintar("sonando", []);
    let k = -1;
    palabras.forEach((p, j) => {
      if (p[1] <= t + 0.05) k = j;
    });
    if (k < 0 || t > palabras[k][2] + 0.6) return pintar("sonando", []); // entre frases no se marca ninguna
    const [a, b] = posiciones(s.texto)[k];
    pintar("sonando", [rango(parrafos.current[activo], a, b)]);
  }, [activo, t, empezo, r.segmentos, utiles]);
  useEffect(() => () => pintar("sonando", []), []);

  // Seguir lo que suena; si la persona hace scroll, se pausa el seguimiento.
  useEffect(() => {
    if (!seguir || !sonando || activo < 0) return;
    scrollPropio.current = true;
    lineas.current[activo]?.scrollIntoView({ block: "center", behavior: "smooth" });
    window.setTimeout(() => (scrollPropio.current = false), 600);
  }, [activo, seguir, sonando]);

  useEffect(() => {
    const alScroll = () => {
      if (!scrollPropio.current && sonando) setSeguir(false);
    };
    const contenedor = document.getElementById("panel-detalle");
    contenedor?.addEventListener("wheel", alScroll, { passive: true });
    contenedor?.addEventListener("touchmove", alScroll, { passive: true });
    return () => {
      contenedor?.removeEventListener("wheel", alScroll);
      contenedor?.removeEventListener("touchmove", alScroll);
    };
  }, [sonando]);

  const irADudosa = useCallback(
    (direccion: 1 | -1) => {
      if (!dudosas.length) return;
      const ahora = audio.current?.currentTime ?? 0;
      const siguiente =
        direccion === 1 ? (dudosas.find((d) => d[2] > ahora + 0.1) ?? dudosas[0]) : ([...dudosas].reverse().find((d) => d[2] < ahora - 0.1) ?? dudosas[dudosas.length - 1]);
      lineas.current[siguiente[0]]?.scrollIntoView({ block: "center", behavior: "smooth" });
      if (audio.current) {
        saltoExplicito.current = true;
        audio.current.currentTime = siguiente[2];
        setT(siguiente[2]);
      }
    },
    [dudosas],
  );

  // Atajos: Ctrl+F busca en la reunión, Espacio reproduce o pausa, J y L saltan 15 s, ] y [ van a la palabra
  // dudosa siguiente o anterior.
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault(); // el WebView tiene su propio buscador: el de la reunión resalta y marca el scroll
        return setBuscando(true);
      }
      const a = audio.current;
      if ((e.target as HTMLElement).closest("input, textarea, select, [contenteditable]") || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "]") return irADudosa(1);
      if (e.key === "[") return irADudosa(-1);
      if (!a) return;
      if (e.key === " ") {
        e.preventDefault();
        a.paused ? a.play() : a.pause();
      } else if (e.key.toLowerCase() === "j") a.currentTime -= 15;
      else if (e.key.toLowerCase() === "l") a.currentTime += 15;
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [irADudosa]);

  const alAvanzar = (a: HTMLAudioElement) => {
    setT(a.currentTime);
    if (!saltear || a.paused) return;
    // En un hueco largo entre turnos se salta al próximo. Con turnos superpuestos, el último que empezó no es el
    // último que termina: se toma el fin más tardío de los que ya empezaron.
    const ahora = a.currentTime;
    const siguiente = r.segmentos.find((s) => s.inicio != null && s.inicio > ahora);
    let finAnterior = 0;
    for (const s of r.segmentos) if (s.inicio != null && s.inicio <= ahora) finAnterior = Math.max(finAnterior, s.fin ?? s.inicio);
    if (siguiente && ahora > finAnterior + 0.3 && siguiente.inicio! - ahora > SILENCIO_LARGO_S) a.currentTime = siguiente.inicio! - 0.3;
  };

  const alReanudar = (a: HTMLAudioElement) => {
    setSonando(true);
    // Al volver de una pausa larga, retroceder 2 s ayuda a retomar el hilo (salvo que se haya elegido un punto).
    if (!saltoExplicito.current && pausadoDesde.current && Date.now() - pausadoDesde.current > PAUSA_LARGA_MS) {
      a.currentTime = Math.max(0, a.currentTime - 2);
    }
    saltoExplicito.current = false;
    pausadoDesde.current = null;
  };

  const copiar = async (texto: string, aviso: string) => {
    try {
      await navigator.clipboard.writeText(texto);
      avisar(aviso);
    } catch {
      avisar("No se pudo copiar", "error");
    }
  };

  // Borrar turnos: texto y audio de ese tramo. Se ocultan ya y se borran a los 10 s si no se deshace.
  const borrarTurnos = (indices: number[]) => {
    const segs = indices.map((i) => r.segmentos[i]).filter((s): s is Segmento => !!s && s.id != null);
    if (!segs.length) return;
    const ids = segs.map((s) => s.id!);
    const tiempos = segs.flatMap((s) => [s.inicio, s.fin]).filter((x): x is number => x != null);
    const tramo = tiempos.length ? ` ${mmss(Math.min(...tiempos))}–${mmss(Math.max(...tiempos))}` : "";
    setOcultos((o) => new Set([...o, ...ids]));
    conDeshacer(
      `Se borra el tramo${tramo}: texto${r.audio ? " y audio" : ""}`,
      async () => {
        try {
          const res = await api<{ audio: boolean }>(`/api/reuniones/${idReunion}/borrar-tramo`, { metodo: "POST", cuerpo: { segmentos: ids } });
          if (res.audio) {
            retomarEn.current = audio.current?.currentTime ?? null; // el audio nuevo ya no tiene ese tramo
            setVersionAudio((v) => v + 1);
          } else if (r.audio) avisar("Se borró el texto, pero el audio de ese tramo no se pudo silenciar", "error");
        } finally {
          alCambiar(); // aun con error, lo que el motor alcanzó a borrar tiene que verse
        }
      },
      () => setOcultos((o) => new Set([...o].filter((id) => !ids.includes(id)))),
    );
  };

  // Corrección a mano. Si cambió una sola palabra, se ofrece cambiar las otras apariciones y sumarla al glosario.
  const guardarTurno = async (s: Segmento, nuevo: string) => {
    setEditando(null);
    const anterior = s.texto;
    try {
      const r2 = await api<{ sugerencia: { original: string; correcto: string; otras: number } | null }>(`/api/reuniones/${idReunion}/segmentos/${s.id}`, {
        metodo: "PATCH",
        cuerpo: { texto: nuevo },
      });
      alCambiar();
      const deshacer = {
        nombre: "Deshacer",
        hacer: async () => {
          await api(`/api/reuniones/${idReunion}/segmentos/${s.id}`, { metodo: "PATCH", cuerpo: { texto: anterior } });
          alCambiar();
        },
      };
      const sug = r2.sugerencia;
      if (!sug) return conAcciones("Turno corregido", [deshacer]);
      conAcciones(`«${sug.original}» → «${sug.correcto}»`, [
        ...(sug.otras
          ? [
              {
                nombre: sug.otras === 1 ? "Cambiar también la otra" : `Cambiar también las otras ${sug.otras}`,
                hacer: async () => {
                  const { cambiados } = await api<{ cambiados: number }>(`/api/reuniones/${idReunion}/reemplazar`, {
                    metodo: "POST",
                    cuerpo: { original: sug.original, correcto: sug.correcto },
                  });
                  alCambiar();
                  avisar(cambiados === 1 ? "Se cambió 1 más" : `Se cambiaron ${cambiados} más`);
                },
              },
            ]
          : []),
        {
          nombre: "Agregar al glosario",
          hacer: async () => {
            await api("/api/glosario/deformacion", { metodo: "POST", cuerpo: { correcto: sug.correcto, deformacion: sug.original } });
            avisar(`El glosario ya sabe que «${sug.original}» es «${sug.correcto}»`);
          },
        },
        deshacer,
      ]);
    } catch (e) {
      avisar(e instanceof Error ? e.message : String(e), "error");
    }
  };

  // Manejadores de la última versión, por una ref estable: los turnos memoizados no se redibujan por ellos.
  const acciones = useRef<AccionesTurno>(null!);
  acciones.current = {
    ir,
    clicEnPalabra: (i, parrafo, x, y) => {
      if (!src || !window.getSelection()?.isCollapsed) return; // seleccionar texto no mueve el audio
      const palabras = utiles[i];
      if (!palabras) return;
      const k = palabraEnPunto(parrafo, x, y);
      if (k != null) ir(palabras[k][1]);
    },
    editar: setEditando,
    guardar: guardarTurno,
    copiarTurno: (s) => copiar(lineaCopiable(s, nombre(s)), "Turno copiado"),
    copiarEnlace: (s) => copiar(enlaceMarkdown(r, s.inicio), "Enlace copiado: pegalo en una nota o en un mensaje"),
    borrar: borrarTurnos,
    registrarLinea: (i, el) => {
      lineas.current[i] = el;
    },
    registrarParrafo: (i, el) => {
      parrafos.current[i] = el;
    },
  };

  const conAudio = !!src;
  const dejarDeSeguir = useCallback(() => setSeguir(false), []);
  const buscador = buscando ? (
    <Buscador segmentos={r.segmentos} parrafos={parrafos} lineas={lineas} cerrar={() => setBuscando(false)} alMoverse={dejarDeSeguir} alCambiar={setHalladas} />
  ) : null;

  return (
    <>
      {conAudio && (
        <>
          <audio
            ref={audio}
            src={src!}
            preload="auto"
            onTimeUpdate={(e) => alAvanzar(e.currentTarget)}
            onLoadedMetadata={(e) => {
              const a = e.currentTarget;
              if (isFinite(a.duration)) setDur(a.duration);
              if (retomarEn.current != null) {
                a.currentTime = retomarEn.current;
                retomarEn.current = null;
              }
            }}
            onPlay={(e) => alReanudar(e.currentTarget)}
            onPause={() => {
              setSonando(false);
              pausadoDesde.current = Date.now();
              saltoExplicito.current = false;
            }}
            onSeeking={(e) => {
              if (e.currentTarget.paused) saltoExplicito.current = true; // barra, J o L con pausa: punto elegido
            }}
            onError={() => {
              // Si el motor se reinició cambian puerto y token: se pide la URL de nuevo una sola vez.
              if (r.grabacion_id) urlAudio(r.grabacion_id).then((nueva) => nueva !== src && setSrc(nueva));
            }}
          />
          <Reproductor
            audio={audio}
            t={t}
            dur={dur}
            sonando={sonando}
            vel={vel}
            setVel={setVel}
            saltear={saltear}
            setSaltear={(v) => {
              setSaltear(v);
              guardarPreferencia("minuta.saltear", v);
            }}
            debajo={
              <>
                <BarraReunion segmentos={r.segmentos} marcas={r.marcas ?? []} coincidencias={halladas} dur={dur} t={t} nombre={nombre} ir={ir} />
                {buscador}
              </>
            }
          />
        </>
      )}

      {!conAudio && buscando && <div className="sticky top-0 z-10 -mx-1 rounded-[10px] border border-linea bg-fondo px-3 pb-2.5">{buscador}</div>}

      {!conAudio && r.fuente === "minuta" && <p className="m-0 text-[12.5px] text-tenue">El audio ya se borró por la retención configurada; queda la transcripción.</p>}

      {dudosas.length > 0 && (
        <p className="m-0 text-[12.5px] text-tenue">
          <span className="dudosa-muestra">{dudosas.length === 1 ? "1 palabra dudosa" : `${dudosas.length} palabras dudosas`}</span> subrayadas · <kbd className="font-mono">]</kbd> y{" "}
          <kbd className="font-mono">[</kbd> para ir de una a otra
        </p>
      )}

      {!!r.marcas?.length && (
        <section aria-label="Marcas" className="flex flex-col gap-1">
          <Rotulo>Marcas que pusiste durante la reunión</Rotulo>
          <ListaMarcas marcas={r.marcas} ir={conAudio ? ir : undefined} alCambiar={alCambiar} />
        </section>
      )}

      <div
        className="flex flex-col gap-[22px]"
        onCopy={(e) => {
          // Ctrl+C sobre la transcripción: cada turno con su minuto y quién habla, listo para pegar.
          const texto = seleccionComoTexto(r.segmentos, parrafos.current, nombre);
          if (!texto) return;
          e.clipboardData.setData("text/plain", texto);
          e.preventDefault();
        }}
      >
        {r.segmentos.map((s, i) =>
          s.id != null && ocultos.has(s.id) ? null : (
            <Turno
              key={s.id ?? i}
              s={s}
              i={i}
              nombre={nombre(s)}
              conAudio={conAudio}
              esActivo={conAudio && i === activo && empezo}
              editando={s.id != null && editando === s.id}
              marcas={marcasPorTurno.get(i)}
              clicable={conAudio && !!utiles[i]}
              acciones={acciones}
            />
          ),
        )}
      </div>

      <BarraSeleccion
        parrafos={parrafos}
        voces={(r.voces ?? []).map((v) => ({ etiqueta: v.etiqueta, nombre: v.nombre ?? v.etiqueta.replace(/^V/, "Voz ") }))}
        vozNueva={vozNueva(r)}
        cambiarVoz={async (indices, etiqueta) => {
          const ids = indices.map((i) => r.segmentos[i]).filter((s) => s?.canal === "sistema" && s.id != null).map((s) => s.id!);
          if (!ids.length) return avisar("Solo se puede cambiar la voz de los turnos de «Ellos»", "error");
          try {
            await api(`/api/reuniones/${idReunion}/cambiar-voz`, { metodo: "POST", cuerpo: { segmentos: ids, etiqueta } });
          } catch (e) {
            alCambiar(); // un 409 es que la reunión cambió: se recarga para volver a elegir sobre lo actual
            return avisar(e instanceof Error ? e.message : String(e), "error");
          }
          alCambiar();
          avisar(ids.length === 1 ? "Turno asignado a otra voz" : `${ids.length} turnos asignados a otra voz`);
        }}
        copiar={() => {
          const texto = seleccionComoTexto(r.segmentos, parrafos.current, nombre);
          if (texto) copiar(texto, "Copiado con minuto y quién habla");
        }}
        borrar={borrarTurnos}
      />

      {conAudio && sonando && !seguir && (
        <button onClick={() => setSeguir(true)} className="aparecer fixed bottom-6 right-8 z-20 rounded-full bg-tinta px-4 py-2.5 text-[13px] text-fondo shadow-flotante">
          Volver a lo que suena
        </button>
      )}
    </>
  );
}
