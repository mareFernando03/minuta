// Cliente de la API local del motor.
// En la app, el supervisor (Rust) lanza el motor y entrega puerto y token por el comando `motor_conexion`.
// En desarrollo en el navegador: `python -m minuta --dev --puerto 8765` y token "dev".

import { useCallback, useEffect, useRef, useState } from "react";
import type { Evento } from "./tipos";

interface Conexion {
  base: string;
  token: string;
}

let conexion: Promise<Conexion> | null = null;

export function enTauri() {
  return "__TAURI_INTERNALS__" in window;
}

// Cuando el supervisor relanza el motor cambian el puerto y el token: se olvida la conexión guardada.
if (enTauri()) {
  import("@tauri-apps/api/event").then(({ listen }) => listen("motor", () => (conexion = null)));
}

export function obtenerConexion(): Promise<Conexion> {
  if (!conexion) {
    conexion = (async () => {
      if (enTauri()) {
        const { invoke } = await import("@tauri-apps/api/core");
        const c = await invoke<{ puerto: number; token: string }>("motor_conexion");
        return { base: `http://127.0.0.1:${c.puerto}`, token: c.token };
      }
      const puerto = import.meta.env.VITE_MOTOR_PUERTO ?? "8765";
      return { base: `http://127.0.0.1:${puerto}`, token: "dev" };
    })();
    conexion.catch(() => (conexion = null));
  }
  return conexion;
}

export class ErrorApi extends Error {
  constructor(public estado: number, mensaje: string) {
    super(mensaje);
  }
}

export async function api<T>(ruta: string, opciones: { metodo?: string; cuerpo?: unknown; texto?: boolean } = {}): Promise<T> {
  const { base, token } = await obtenerConexion();
  let r: Response;
  try {
    r = await fetch(base + ruta, {
      method: opciones.metodo ?? "GET",
      headers: { Authorization: `Bearer ${token}`, ...(opciones.cuerpo !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: opciones.cuerpo !== undefined ? JSON.stringify(opciones.cuerpo) : undefined,
    });
  } catch {
    conexion = null; // el motor pudo reiniciarse con otro puerto
    throw new ErrorApi(0, "No se pudo hablar con el motor de Minuta. Se está reiniciando o no arrancó.");
  }
  if (!r.ok) {
    let detalle = r.statusText;
    try {
      detalle = (await r.json()).detail ?? detalle;
    } catch {
      /* sin cuerpo JSON */
    }
    throw new ErrorApi(r.status, String(detalle));
  }
  return (opciones.texto ? r.text() : r.json()) as Promise<T>;
}

export async function urlAudio(grabacionId: string) {
  const { base, token } = await obtenerConexion();
  return `${base}/api/grabaciones/${grabacionId}/audio?t=${encodeURIComponent(token)}`;
}

/** Pide una ruta y la vuelve a pedir cuando cambian las dependencias o se llama `recargar`. */
export function useDatos<T>(ruta: string | null, deps: unknown[] = []) {
  const [datos, setDatos] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const vuelta = useRef(0);

  const recargar = useCallback(async () => {
    if (!ruta) return;
    const esta = ++vuelta.current;
    setCargando(true);
    try {
      const d = await api<T>(ruta);
      if (esta === vuelta.current) {
        setDatos(d);
        setError(null);
      }
    } catch (e) {
      if (esta === vuelta.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (esta === vuelta.current) setCargando(false);
    }
  }, [ruta]);

  useEffect(() => {
    recargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recargar, ...deps]);

  return { datos, error, cargando, recargar, setDatos };
}

/** Eventos en vivo del motor (WebSocket), con reconexión. */
export function useEventos(alRecibir: (e: Evento) => void) {
  const ref = useRef(alRecibir);
  ref.current = alRecibir;
  useEffect(() => {
    let ws: WebSocket | null = null;
    let cerrado = false;
    let espera = 500;
    let temporizador: number | undefined;
    const conectar = async () => {
      try {
        const { base, token } = await obtenerConexion();
        ws = new WebSocket(`${base.replace("http", "ws")}/api/eventos?t=${encodeURIComponent(token)}`);
        ws.onopen = () => (espera = 500);
        ws.onmessage = (m) => ref.current(JSON.parse(m.data));
        ws.onclose = () => {
          conexion = null; // si el motor se reinició, la próxima vuelta pide puerto y token nuevos
          if (!cerrado) temporizador = window.setTimeout(conectar, (espera = Math.min(espera * 2, 10000)));
        };
      } catch {
        if (!cerrado) temporizador = window.setTimeout(conectar, (espera = Math.min(espera * 2, 10000)));
      }
    };
    conectar();
    return () => {
      cerrado = true;
      window.clearTimeout(temporizador);
      ws?.close();
    };
  }, []);
}
