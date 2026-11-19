// Formas de lo que devuelve la API del motor (motor/minuta/api/servidor.py).

export type Canal = "mic" | "sistema" | string;

/** [texto, inicio_s, fin_s, probabilidad | null], 1 a 1 con `texto.split(/\s+/)`. */
export type Palabra = [string, number, number, number | null];

export interface Segmento {
  id?: number;
  canal: Canal;
  inicio: number | null;
  fin: number | null;
  texto: string;
  palabras?: Palabra[] | null; // las reuniones de Granola y las anteriores al 10/10 no tienen
  voz?: string | null; // dentro de «Ellos»: V1, V2… (diarización)
}

/** Una voz que la diarización encontró en una reunión, con su persona si se sabe. */
export interface Voz {
  etiqueta: string;
  segundos: number | null;
  voz_id: number | null;
  nombre: string | null;
  origen: "manual" | "auto" | null;
  sugerida: { id: number; nombre: string; similitud: number } | null;
}

export interface Participante {
  mail: string;
  nombre: string;
  estado?: string;
}

export interface ReunionResumen {
  id: string;
  fuente: "minuta" | "granola";
  titulo: string;
  carpeta: string | null;
  inicio: string;
  fin: string | null;
  motor: string | null;
  origen: string | null;
}

export interface Reunion extends ReunionResumen {
  grabacion_id: string | null;
  participantes: Participante[];
  resumen: string | null;
  correcciones: { original: string; correcto: string }[];
  hablantes: Record<string, string>;
  exportado_en: string | null;
  segmentos: Segmento[];
  marcas: Marca[];
  voces?: Voz[];
  resumen_minuta?: ResumenMinuta | null;
  acciones?: Accion[];
  ediciones?: Edicion[];
  audio: boolean;
  audio_hasta: string | null;
  /** Motores con los que se puede volver a transcribir: lo que no sale de la PC solo trae "local". */
  motores?: string[];
  /** Si Claude la puede leer (MCP). */
  sale?: boolean;
}

/** El resumen propio de Minuta: cada viñeta con los segundos de la reunión que la respaldan. */
export interface ResumenMinuta {
  secciones: { titulo: string; vinetas: { texto: string; citas: number[] }[] }[];
  generado: string;
  pedido: string | null;
  con_plantilla: boolean;
}

export interface Accion {
  id: number;
  reunion_id: string;
  texto: string;
  responsable: string | null;
  t: number | null;
  estado: "propuesta" | "aceptada" | "descartada";
}

/** Corrección que Claude propuso por MCP (propose_edit): el usuario la aplica o la rechaza. */
export interface Edicion {
  id: number;
  segmento_id: number;
  original: string;
  propuesto: string;
  motivo: string | null;
  inicio: number | null;
}

/** Lo que conviene saber al entrar a una reunión del calendario, armado con la anterior de su serie. */
export interface Brief {
  evento: { titulo: string; inicio: string };
  anterior: { id: string; titulo: string; inicio: string; resumen: string[]; acciones: Accion[]; marcas: Marca[] } | null;
}

export interface ResultadoBusqueda {
  id: string;
  titulo: string;
  carpeta: string | null;
  fecha: string;
  segundo: number | null;
  canal: string;
  fragmento: string;
}

export interface Trabajo {
  id: number;
  tipo: "transcribir" | "repasar" | "diarizar" | "resumir";
  grabacion_id: string;
  estado: "pendiente" | "corriendo" | "hecho" | "error";
  intentos: number;
  progreso: string | null;
  error: string | null;
  actualizado: string;
  avance: number | null;
  restante_s: number | null;
  inicio: string | null;
  titulo: string | null;
  ventanas: string[];
  apps: string[];
}

export interface GrabacionConError {
  grabacion_id: string;
  inicio: string | null;
  titulo: string | null;
  ventanas: string[];
  apps: string[];
  error: string | null;
}

export interface Grabacion {
  id: string;
  inicio: string;
  fin: string | null;
  duracion_s: number | null;
  apps: string[];
  estado: string;
  error: string | null;
  reunion_id: string | null;
  audio_hasta: string | null;
}

export type EtiquetaMarca = "accion" | "decision" | "pregunta";

export interface Marca {
  id: number;
  grabacion_id: string;
  reunion_id: string | null;
  t: number;
  etiqueta: EtiquetaMarca | null;
  nota: string | null;
  creado: string;
}

export interface Alerta {
  tipo: "mic_mudo" | "sin_senal" | "sistema_mudo" | "dispositivo" | "silencio";
  mensaje: string;
  desde: string;
  vence: string | null;
}

export interface Ahora {
  grabando: boolean;
  pausado: boolean;
  /** No se puede ver quién usa el micrófono: las reuniones no se graban solas. */
  sin_deteccion?: boolean;
  id?: string;
  apps?: string[];
  /** Grabación empezada a mano (presencial): no se corta al soltar el micrófono. */
  manual?: boolean;
  /** El título que se le dio al grabar a mano. */
  titulo?: string | null;
  segundos?: number;
  soltado?: boolean;
  solo_local?: boolean;
  niveles?: { mic: number; sistema: number };
  dispositivos?: { mic: string; salida: string };
  ventanas?: string[];
  alertas?: Alerta[];
  marcas?: Marca[];
}

export interface Estado {
  ahora: Ahora;
  cola: Trabajo[];
  para_revisar: number;
  con_error: number;
  errores: GrabacionConError[];
}

export interface Motores {
  configurados: { assemblyai: boolean; groq: boolean; calendarios: string[] };
  assemblyai: { agotado: boolean; gastado_usd: number; credito_estimado_usd: number };
  groq: { hoy_s: number; cuota_diaria_s: number };
  haiku: { gastado_usd: number };
  local: { segundos: number; enchufada: boolean };
}

export interface Glosario {
  prompt: string;
  terminos: string;
  tokens_prompt: number | null;
  limite_tokens: number;
}

export interface Ajustes {
  privacidad: Record<string, string[]>;
  audio_dias: number;
  exportar_a: string;
  notificaciones: boolean;
  diarizar: boolean;
  diarizacion_auto: boolean;
  resumir: boolean;
  plantillas: Record<string, string>;
  dictado: boolean;
  dictado_motor: "groq" | "local";
  carpetas: string[];
  secretos: { assemblyai: boolean; groq: boolean; calendarios: string[] };
}

export interface Evento {
  tipo: string;
  datos: any;
  en: string;
}
