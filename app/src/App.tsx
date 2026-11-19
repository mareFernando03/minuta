import { useCallback, useEffect, useRef, useState } from "react";
import { api, enTauri, useEventos } from "./api/cliente";
import type { Estado, Evento, Motores as M } from "./api/tipos";
import { ProveedorAvisos, useAvisos } from "./componentes/Avisos";
import { Barra, type Seccion } from "./componentes/Barra";
import { ErrorCaja } from "./componentes/Basicos";
import { HojaAtajos } from "./componentes/HojaAtajos";
import { Paleta } from "./componentes/Paleta";
import { Ahora } from "./pantallas/Ahora";
import { Ajustes } from "./pantallas/Ajustes";
import { Biblioteca } from "./pantallas/Biblioteca";
import { Glosario } from "./pantallas/Glosario";
import { Motores } from "./pantallas/Motores";
import type { Salto } from "./pantallas/detalle/Transcripcion";
import { Revisar } from "./pantallas/Revisar";
import { configurarNotificaciones, notificarEvento } from "./util/notificar";

export default function App() {
  return (
    <ProveedorAvisos>
      <Cuerpo />
    </ProveedorAvisos>
  );
}

function Cuerpo() {
  // Arranca en Reuniones; si hay una reunión grabándose, en Ahora (lo decide el primer estado).
  const [seccion, setSeccion] = useState<Seccion>("reuniones");
  const montada = useRef(Date.now());
  const [reunion, setReunion] = useState<string | null>(null);
  const [estado, setEstado] = useState<Estado | null>(null);
  const [motores, setMotores] = useState<M | null>(null);
  const [sinMotor, setSinMotor] = useState<string | null>(null);
  const [paleta, setPaleta] = useState(false);
  // Un salto a un minuto se cumple una vez: al salir de Reuniones se olvida, así no se repite al volver a montar.
  const [salto, setSalto] = useState<Salto | null>(null);
  useEffect(() => {
    if (seccion !== "reuniones") setSalto(null);
  }, [seccion]);
  const [atajos, setAtajos] = useState(false);
  const { avisar } = useAvisos();

  const refrescar = useCallback(async () => {
    try {
      const e = await api<Estado>("/api/estado");
      setEstado(e);
      setSinMotor(null);
      // Solo al arrancar (el motor tarda unos segundos en responder) y si todavía no se fue de Reuniones: una
      // grabación que empieza después no le cambia la pantalla a nadie.
      if (montada.current && e.ahora.grabando && Date.now() - montada.current < 20_000) setSeccion((s) => (s === "reuniones" ? "ahora" : s));
      montada.current = 0;
    } catch (e) {
      setSinMotor(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refrescar();
    api<M>("/api/motores").then(setMotores).catch(() => {});
    api<{ notificaciones: boolean }>("/api/ajustes").then((a) => configurarNotificaciones(a.notificaciones)).catch(() => {});
    const t = window.setInterval(refrescar, 5000); // respaldo por si se corta el WebSocket
    return () => window.clearInterval(t);
  }, [refrescar]);

  useEventos(
    useCallback(
      (e: Evento) => {
        if (e.tipo === "ahora") return setEstado((s) => (s ? { ...s, ahora: e.datos } : s));
        if (e.tipo === "dictado") return; // lo dibuja la píldora, diez veces por segundo: no es un cambio de estado
        refrescar();
        notificarEvento(e);
        // Una reunión cambió por debajo (se separaron las voces): el detalle abierto se recarga con los turnos nuevos.
        if (e.tipo === "brief") window.dispatchEvent(new CustomEvent("minuta:brief"));
        if (e.tipo === "resumen") window.dispatchEvent(new CustomEvent("minuta:resumen", { detail: e.datos }));
        if (e.tipo === "voces" || e.tipo === "resumen") window.dispatchEvent(new CustomEvent("minuta:reunion-cambio", { detail: e.datos.id }));
        if (e.tipo === "reunion_lista") {
          api<M>("/api/motores").then(setMotores).catch(() => {});
          avisar(e.datos.revisar ? "Hay una grabación para revisar" : `Lista: «${e.datos.titulo}»`);
        }
        if (e.tipo === "error") avisar(`No se pudo procesar una grabación: ${e.datos.error}`, "error");
      },
      [refrescar, avisar],
    ),
  );

  // El panel de la bandeja pide abrir una sección o una reunión.
  useEffect(() => {
    if (!enTauri()) return;
    // Lo mismo para un enlace minuta://reunion/<id>?t=… (el de arranque queda pendiente hasta montar).
    const navegar = (d: { seccion: Seccion; reunion?: string; t?: number | null }) => {
      if (d.reunion) setReunion(d.reunion);
      // Un enlace externo posiciona en el minuto pero no reproduce: el audio de una reunión no arranca solo.
      if (d.t != null) setSalto((s) => ({ t: d.t!, n: (s?.n ?? 0) + 1, reproducir: false }));
      setSeccion(d.seccion);
    };
    const quitar = import("@tauri-apps/api/event").then(({ listen }) => listen<Parameters<typeof navegar>[0]>("navegar", ({ payload }) => navegar(payload)));
    import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke<Parameters<typeof navegar>[0] | null>("enlace_pendiente"))
      .then((d) => d && navegar(d))
      .catch(() => {});
    return () => void quitar.then((f) => f());
  }, []);

  const pausar = useCallback(
    async (pausado: boolean) => {
      await api("/api/pausa", { metodo: "POST", cuerpo: { pausado } });
      refrescar();
      avisar(pausado ? "Grabaciones en pausa" : "Minuta vuelve a grabar");
    },
    [refrescar, avisar],
  );

  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === "k") (e.preventDefault(), setPaleta((p) => !p));
      if (k === "/") (e.preventDefault(), setAtajos((a) => !a));
      const ir: Record<string, Seccion> = { "1": "ahora", "2": "reuniones", "3": "revisar", "4": "motores", "5": "glosario", ",": "ajustes" };
      if (ir[k]) (e.preventDefault(), setSeccion(ir[k]));
      if (e.shiftKey && k === "p") (e.preventDefault(), pausar(!estado?.ahora.pausado));
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [pausar, estado?.ahora.pausado]);

  // Mientras se mantiene Alt se ven los atajos sobre los botones; si no, no ensucian.
  useEffect(() => {
    const ver = (v: boolean) => document.body.classList.toggle("ver-atajos", v);
    const abajo = (e: KeyboardEvent) => e.key === "Alt" && (e.preventDefault(), ver(true));
    const arriba = (e: KeyboardEvent) => e.key === "Alt" && ver(false);
    const sale = () => ver(false);
    window.addEventListener("keydown", abajo);
    window.addEventListener("keyup", arriba);
    window.addEventListener("blur", sale);
    return () => {
      window.removeEventListener("keydown", abajo);
      window.removeEventListener("keyup", arriba);
      window.removeEventListener("blur", sale);
    };
  }, []);

  return (
    <div className="flex h-full">
      <Barra actual={seccion} ir={setSeccion} estado={estado} motores={motores} buscar={() => setPaleta(true)} />
      <div className="flex min-w-0 flex-1 flex-col">
        {sinMotor && <ErrorCaja mensaje={sinMotor} reintentar={refrescar} />}
        {seccion === "ahora" && (
          <Ahora
            estado={estado}
            refrescar={refrescar}
            abrir={(id) => {
              setReunion(id);
              setSeccion("reuniones");
            }}
          />
        )}
        {seccion === "reuniones" && <Biblioteca estado={estado} seleccion={reunion} seleccionar={setReunion} salto={salto} />}
        {seccion === "revisar" && <Revisar alCambiar={refrescar} />}
        {seccion === "motores" && <Motores />}
        {seccion === "glosario" && <Glosario />}
        {seccion === "ajustes" && <Ajustes />}
      </div>
      <Paleta
        abierta={paleta}
        cerrar={() => setPaleta(false)}
        ir={setSeccion}
        abrirReunion={(id, segundo) => {
          setReunion(id);
          setSeccion("reuniones");
          if (segundo != null) setSalto((s) => ({ t: segundo, n: (s?.n ?? 0) + 1, reproducir: false }));
        }}
        estado={estado}
        alPausar={pausar}
        verAtajos={() => setAtajos(true)}
      />
      <HojaAtajos abierta={atajos} cerrar={() => setAtajos(false)} />
    </div>
  );
}
