const dia = new Intl.DateTimeFormat("es-AR", { weekday: "long", day: "numeric", month: "long" });
const hora = new Intl.DateTimeFormat("es-AR", { hour: "2-digit", minute: "2-digit", hour12: false });
const corta = new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "numeric" });

export const fecha = (iso: string) => new Date(iso);

export function horaDe(iso: string) {
  return hora.format(fecha(iso));
}

export function fechaCorta(iso: string) {
  return corta.format(fecha(iso));
}

function mismaFecha(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** «Hoy · jueves 9 de octubre», «Ayer · miércoles 8», «lunes 6 de octubre». */
export function grupoDeDia(iso: string, ahora = new Date()) {
  const d = fecha(iso);
  const ayer = new Date(ahora);
  ayer.setDate(ahora.getDate() - 1);
  const texto = dia.format(d);
  if (mismaFecha(d, ahora)) return `Hoy · ${texto}`;
  if (mismaFecha(d, ayer)) return `Ayer · ${texto.replace(/ de \w+$/, "")}`;
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

export function diaCompleto(iso: string) {
  const t = dia.format(fecha(iso));
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function mmss(segundos: number | null | undefined) {
  if (segundos == null || !isFinite(segundos)) return "--:--";
  const s = Math.max(0, Math.floor(segundos));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${String(m).padStart(2, "0")}:${r}`;
}

export function minutos(inicio: string, fin: string | null) {
  if (!fin) return null;
  return Math.max(1, Math.round((fecha(fin).getTime() - fecha(inicio).getTime()) / 60000));
}

export const NOMBRE_MOTOR: Record<string, string> = {
  assemblyai: "AssemblyAI",
  groq: "Groq",
  small: "Whisper local",
  "large-v3-turbo": "Whisper local (turbo)",
  granola: "Granola",
  local: "Whisper local",
};

export const EN_LA_NUBE = new Set(["assemblyai", "groq"]);

export function usd(n: number) {
  return `US$${n.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** «…la base [[garantiza]] la…» → partes para resaltar con <mark>. */
export function partesResaltadas(fragmento: string) {
  return fragmento.split(/(\[\[.*?\]\])/g).map((p) => (p.startsWith("[[") ? { marca: true, texto: p.slice(2, -2) } : { marca: false, texto: p }));
}

const APP: Record<string, string> = { "chrome.exe": "Chrome", "msedge.exe": "Edge", "slack.exe": "Slack" };

/** Nombre para una reunión que todavía no tiene título: el del huddle o de la pestaña de Meet o Teams. */
export function tituloProvisorio(ventanas: string[] = [], apps: string[] = [], titulo?: string | null) {
  if (titulo) return titulo;
  if (apps.includes("manual")) return "Reunión presencial";
  const huddle = ventanas.find((v) => v.includes("🔊"));
  if (huddle) return `Huddle · ${huddle.replace(/\s*\((Canal|Mensajes directos)\)|\s+-\s.*$/g, "")}`;
  const meet = ventanas.find((v) => v.startsWith("Meet - ") || v.includes("| Microsoft Teams"));
  if (meet)
    return meet
      .replace(/^Meet - |\s*\| Microsoft Teams.*$| - Google Chrome$/g, "")
      .replace(/^(\(\d+\) )?(Equipos y canales|Teams y canales|Teams and channels) \| /, ""); // la sección de Teams
  return `Reunión en ${apps.map((x) => APP[x] ?? x).join(" y ") || "curso"}`;
}

/** «faltan ~3 min» o «falta menos de un minuto». */
export function faltan(segundos: number | null | undefined) {
  if (segundos == null) return null;
  if (segundos < 60) return "falta menos de un minuto";
  return `faltan ~${Math.round(segundos / 60)} min`;
}
