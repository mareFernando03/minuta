// Motor simulado para los tests: rutas de la API con datos de ejemplo y un WebSocket de eventos controlable.
//
// Orden de registro (page.route es LIFO: gana el último registrado): primero el catch-all, que responde 404 a lo
// que no se simuló, y después las rutas específicas. Las regex no llevan `$` para no perder sub-rutas.

import type { Page, WebSocketRoute } from "@playwright/test";

export const MOTOR = "http://127.0.0.1:8799";

export const REUNION = {
  id: "local-1",
  fuente: "minuta",
  titulo: "Daily Orbit",
  carpeta: "Orbit",
  inicio: "2026-10-10T09:30:00",
  fin: "2026-10-10T09:45:00",
  motor: "assemblyai",
  origen: "calendario",
  grabacion_id: "20261010-093000",
  participantes: [],
  resumen: null,
  correcciones: [{ original: "Contosó", correcto: "Contoso" }],
  hablantes: {},
  exportado_en: "2026-10-10T09:50:00",
  segmentos: [
    { id: 1, canal: "sistema", inicio: 5, fin: 9, texto: "Arrancamos con el estado del despliegue de Contosó." },
    { id: 2, canal: "mic", inicio: 10, fin: 14, texto: "Yo terminé el script de la base." },
    { id: 3, canal: "sistema", inicio: 40, fin: 48, texto: "Perfecto, entonces lo pasamos a producción el jueves." },
  ],
  marcas: [{ id: 7, grabacion_id: "20261010-093000", reunion_id: "local-1", t: 42, etiqueta: "decision", nota: "Producción el jueves", creado: "" }],
  audio: false,
  audio_hasta: null,
};

export const REUNION_VOCES = {
  ...REUNION,
  id: "local-2",
  titulo: "Refinamiento Northwind",
  segmentos: [
    { id: 11, canal: "sistema", inicio: 2, fin: 6, texto: "Arranco con la historia de facturación.", voz: "V1" },
    { id: 12, canal: "sistema", inicio: 7, fin: 12, texto: "Yo tengo dudas con el contrato.", voz: "V2" },
    { id: 13, canal: "mic", inicio: 13, fin: 15, texto: "Lo vemos después." },
  ],
  marcas: [],
  voces: [
    { etiqueta: "V1", segundos: 240, voz_id: null, nombre: null, origen: null, sugerida: { id: 1, nombre: "Pedro Ríos", similitud: 0.63 } },
    { etiqueta: "V2", segundos: 30, voz_id: null, nombre: null, origen: null, sugerida: null },
  ],
};

export const REUNION_RESUMIDA = {
  ...REUNION,
  id: "local-3",
  titulo: "Planning GLOBEX",
  carpeta: "Globex",
  sale: true,
  marcas: [],
  resumen_minuta: {
    secciones: [{ titulo: "Decisiones", vinetas: [{ texto: "El script de la base pasa a producción el jueves.", citas: [10, 40] }] }],
    generado: "2026-10-10T10:00:00",
    pedido: null,
    con_plantilla: false,
  },
  acciones: [{ id: 21, reunion_id: "local-3", texto: "Pasar el script a producción", responsable: "Vos", t: 40, estado: "propuesta" }],
  ediciones: [{ id: 5, segmento_id: 1, original: "Arrancamos con el estado del despliegue de Contosó.", propuesto: "Arrancamos con el estado del despliegue de Contoso.", motivo: "nombre del cliente", inicio: 5 }],
};

export const BRIEF = {
  evento: { titulo: "Daily Orbit", inicio: new Date(Date.now() + 9 * 60_000).toISOString().slice(0, 19) },
  anterior: { id: "local-1", titulo: "Daily Orbit", inicio: "2026-10-10T09:30:00", resumen: ["Producción el jueves"], acciones: [{ id: 30, reunion_id: "local-1", texto: "Mandar el script", responsable: "Vos", t: 10, estado: "aceptada" }], marcas: [] },
};

export const ESPERA = { grabando: false, pausado: false };

export const GRABANDO = {
  grabando: true,
  pausado: false,
  id: "20261010-110000",
  apps: ["chrome.exe"],
  solo_local: false,
  segundos: 754,
  soltado: false,
  niveles: { mic: 0.05, sistema: 0.08 },
  dispositivos: { mic: "Micrófono (Realtek)", salida: "Parlantes" },
  ventanas: ["Meet - Refinamiento Northwind - Google Chrome"],
  alertas: [] as unknown[],
  marcas: [] as unknown[],
};

export interface Motor {
  estado: { ahora: unknown; cola: unknown[]; para_revisar: number; con_error: number; errores: unknown[] };
  pedidos: { metodo: string; ruta: string; cuerpo: unknown }[];
  eventos: () => WebSocketRoute | null;
}

export async function simularMotor(page: Page, ahora: unknown = ESPERA, brief: unknown = null): Promise<Motor> {
  const m: Motor = {
    estado: { ahora, cola: [], para_revisar: 0, con_error: 0, errores: [] },
    pedidos: [],
    eventos: () => ws,
  };
  let ws: WebSocketRoute | null = null;
  await page.routeWebSocket(/127\.0\.0\.1:8799\/api\/eventos/, (r) => {
    ws = r; // no se conecta a ningún servidor: el test manda los eventos con ws.send
  });

  const json = (cuerpo: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(cuerpo) });

  // 1) Catch-all: lo que no se simuló responde 404 (y queda registrado para depurar).
  await page.route(/127\.0\.0\.1:8799\//, (route) => route.fulfill(json({ detail: `no simulado: ${route.request().url()}` }, 404)));

  // 2) Rutas específicas (registradas después: tienen prioridad).
  await page.route(/127\.0\.0\.1:8799\/api\/(estado|motores|ajustes|reuniones|buscar|glosario|grabaciones|marcas|pausa|grabacion-actual|voces|acciones|ediciones|brief)/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const ruta = url.pathname;
    const metodo = req.method();
    if (metodo === "OPTIONS") return route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
    const cuerpo = req.postData() ? JSON.parse(req.postData()!) : null;
    if (metodo !== "GET") m.pedidos.push({ metodo, ruta, cuerpo });
    const cors = { "access-control-allow-origin": "*" };
    const responder = (c: unknown, status = 200) => route.fulfill({ ...json(c, status), headers: cors });

    if (ruta === "/api/estado") return responder(m.estado);
    if (ruta === "/api/motores")
      return responder({
        configurados: { assemblyai: true, groq: true, calendarios: [] },
        assemblyai: { agotado: false, gastado_usd: 1.2, credito_estimado_usd: 48.8 },
        groq: { hoy_s: 600, cuota_diaria_s: 28800 },
        haiku: { gastado_usd: 0 },
        local: { segundos: 0, enchufada: true },
      });
    if (ruta === "/api/ajustes")
      return responder({ privacidad: { "*": ["groq", "local"] }, audio_dias: 7, exportar_a: "C:\\export", notificaciones: true, diarizar: true, diarizacion_auto: false, resumir: true, plantillas: {}, dictado: false, dictado_motor: "groq", carpetas: ["Orbit"], secretos: { assemblyai: true, groq: true, calendarios: [] } });
    if (ruta === "/api/reuniones" && metodo === "GET")
      return responder([
        REUNION,
        { ...REUNION, id: "local-2", titulo: "Refinamiento Northwind", carpeta: "Seguimiento Northwind", inicio: "2026-10-09T15:00:00" },
        { ...REUNION, id: "local-3", titulo: "Planning GLOBEX", carpeta: "Globex", inicio: "2026-10-08T15:00:00" },
      ]);
    if (ruta === "/api/brief") return responder(brief);
    if (ruta.endsWith("/resumir")) return responder({ trabajo: 9 });
    if (ruta.startsWith("/api/acciones/")) return responder({ ...REUNION_RESUMIDA.acciones[0], ...cuerpo });
    if (ruta.startsWith("/api/ediciones/")) return responder({ ok: true });
    if (ruta.startsWith("/api/reuniones/local-3") && metodo === "GET") return responder(REUNION_RESUMIDA);
    if (ruta.startsWith("/api/reuniones/local-1") && metodo === "GET") return responder(REUNION);
    if (ruta.startsWith("/api/reuniones/local-2") && metodo === "GET") return responder(REUNION_VOCES);
    if (/\/voces\/V\d+\/(nombre|rechazar)/.test(ruta)) return responder({ voces: REUNION_VOCES.voces });
    if (ruta === "/api/voces") return responder([{ id: 1, nombre: "Pedro Ríos", n: 2, reuniones: 3 }]);
    if (/\/segmentos\/\d+/.test(ruta) && metodo === "PATCH")
      return responder({ segmento: { id: 1, texto: cuerpo.texto }, sugerencia: /Contoso/.test(cuerpo.texto) ? { original: "Contosó", correcto: "Contoso", otras: 2 } : null });
    if (ruta.endsWith("/reemplazar")) return responder({ cambiados: 2 });
    if (ruta.endsWith("/borrar-tramo")) return responder({ borrados: cuerpo.segmentos.length, desde: 5, hasta: 14, audio: true });
    if (ruta === "/api/glosario/deformacion") return responder({ linea: "Contoso <- Contosó" });
    if (ruta.startsWith("/api/reuniones/") && metodo === "PATCH") return responder({ ...REUNION, ...cuerpo });
    if (ruta === "/api/buscar")
      return responder([{ id: "local-1", titulo: "Daily Orbit", carpeta: "Orbit", fecha: "2026-10-10T09:30:00", segundo: 40, canal: "sistema", fragmento: "lo pasamos a [[producción]] el jueves" }]);
    if (ruta === "/api/grabacion-actual/marca") return responder({ id: 99, grabacion_id: "x", reunion_id: null, t: 754, etiqueta: null, nota: null, creado: "" });
    if (ruta.startsWith("/api/grabacion-actual/") || ruta === "/api/pausa") return responder({ ok: true });
    if (ruta.startsWith("/api/marcas/")) return responder({ ...REUNION.marcas[0], ...cuerpo });
    if (ruta.includes("/reprocesar")) return responder({ trabajo: 5 });
    if (ruta === "/api/glosario")
      return responder({ prompt: "Reunión con Lucía Benítez y Pedro Ríos.", terminos: "Contoso <- Contosó", tokens_prompt: 12, limite_tokens: 223 });
    return route.fallback();
  });
  return m;
}
