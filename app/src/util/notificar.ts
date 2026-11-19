// Notificaciones nativas de Windows. Las dispara la ventana principal (sigue viva aunque esté oculta en la bandeja)
// a partir de los eventos del motor: una por evento, y ninguna si la app ya está en primer plano mostrando lo mismo.
// En desarrollo Windows las muestra como «PowerShell»; con la app instalada salen con el nombre y el ícono de Minuta.

import { enTauri } from "../api/cliente";
import type { Evento } from "../api/tipos";
import { mmss, tituloProvisorio } from "./formato";

let activas = true;

export function configurarNotificaciones(valor: boolean) {
  activas = valor;
}

export async function notificar(titulo: string, cuerpo?: string) {
  if (!enTauri() || !activas) return;
  if (document.visibilityState === "visible" && document.hasFocus()) return; // la app ya lo está mostrando
  const n = await import("@tauri-apps/plugin-notification");
  if (!(await n.isPermissionGranted()) && (await n.requestPermission()) !== "granted") return;
  n.sendNotification({ title: titulo, body: cuerpo });
}

const ALERTAS: Record<string, string> = {
  mic_mudo: "El micrófono no capta nada",
  sin_senal: "El micrófono dejó de mandar audio",
  sistema_mudo: "No se escucha a los demás",
  silencio: "¿Sigue la reunión?",
};

/** Qué evento del motor merece una notificación, y con qué texto. */
export function notificarEvento(e: Evento) {
  const d = e.datos;
  switch (e.tipo) {
    case "grabacion_empezada":
      return notificar("Minuta está grabando", `${tituloProvisorio([], d.apps)}. Si no es una reunión, descartala desde el ícono de la bandeja.`);
    case "alerta":
      return ALERTAS[d.tipo] && notificar(ALERTAS[d.tipo], d.mensaje);
    case "marca": // una nota se escribe en la app: no hace falta avisar que se guardó
      return !d.nota && notificar(`Marcado en ${mmss(d.t)}`, "La etiqueta y la nota se agregan en «Ahora» o en el detalle.");
    case "brief":
      return notificar(
        "Tu próxima reunión empieza en 1 minuto",
        d.anterior ? "Lo que quedó de la anterior está en «Ahora», en Minuta." : "Minuta va a grabarla cuando empiece.",
      );
    // Sin el título ni el detalle: la notificación sale aunque estés compartiendo pantalla en la reunión siguiente,
    // y queda en el Centro de actividades y en la pantalla de bloqueo.
    case "reunion_lista":
      return notificar(
        d.revisar ? "Hay una grabación para revisar" : "Una reunión está lista",
        d.revisar ? "No se identificó como reunión: se transcribió solo en esta PC." : "Ya está en la biblioteca de Minuta.",
      );
    case "error":
      return notificar("No se pudo procesar una grabación", "El detalle está en Minuta.");
  }
}
