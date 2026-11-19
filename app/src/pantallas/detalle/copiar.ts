// Copiar desde la transcripción con formato listo para pegar en una nota o en un mensaje:
//   [12:34] Pedro: lo pasamos a producción el jueves
// y enlaces que abren Minuta en ese minuto: [Daily Orbit · 12:34](minuta://reunion/<id>?t=754)

import type { Reunion, Segmento } from "../../api/tipos";
import { mmss } from "../../util/formato";

export function lineaCopiable(s: Segmento, nombre: string, texto = s.texto) {
  return `${s.inicio != null ? `[${mmss(s.inicio)}] ` : ""}${nombre}: ${texto.trim()}`;
}

export function enlace(r: Reunion, segundo: number | null) {
  const t = segundo != null ? `?t=${Math.floor(segundo)}` : "";
  return `minuta://reunion/${encodeURIComponent(r.id)}${t}`;
}

export function enlaceMarkdown(r: Reunion, segundo: number | null) {
  return `[${r.titulo}${segundo != null ? ` · ${mmss(segundo)}` : ""}](${enlace(r, segundo)})`;
}

/** Texto de la selección actual, turno por turno con su minuto y quién habla. Null si no toca la transcripción. */
export function seleccionComoTexto(segmentos: Segmento[], parrafos: (HTMLParagraphElement | null)[], nombre: (s: Segmento) => string) {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const r = sel.getRangeAt(0);
  const lineas: string[] = [];
  segmentos.forEach((s, i) => {
    const p = parrafos[i];
    const nodo = p?.firstChild;
    if (!p || !nodo || !r.intersectsNode(p)) return;
    const desde = r.startContainer === nodo ? r.startOffset : 0;
    const hasta = r.endContainer === nodo ? r.endOffset : s.texto.length;
    const fragmento = s.texto.slice(desde, hasta);
    if (fragmento.trim()) lineas.push(lineaCopiable(s, nombre(s), fragmento));
  });
  return lineas.length ? lineas.join("\n") : null;
}
