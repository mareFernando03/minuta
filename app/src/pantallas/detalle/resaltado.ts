// Resaltado sin tocar el DOM, con la CSS Custom Highlight API (WebView2 y Chrome la tienen): la palabra que suena,
// las dudosas y las coincidencias de búsqueda se pintan como rangos sobre el texto de cada turno. Así no hay un span
// por palabra ni se vuelve a dibujar la transcripción cada vez que avanza el audio.

import type { Palabra, Segmento } from "../../api/tipos";

export const hayResaltado = typeof CSS !== "undefined" && "highlights" in CSS;

/** Posición [inicio, fin) de cada palabra del texto (separadas por espacios), en caracteres. */
export function posiciones(texto: string): [number, number][] {
  return Array.from(texto.matchAll(/\S+/g), (m) => [m.index!, m.index! + m[0].length]);
}

/** Las palabras sirven solo si están 1 a 1 con el texto (las viejas o editadas sin alinear, no). */
export function palabrasUtiles(s: Segmento): Palabra[] | null {
  const p = s.palabras;
  return p && p.length === posiciones(s.texto).length ? p : null;
}

export function rango(parrafo: HTMLElement | null | undefined, ini: number, fin: number): Range | null {
  const nodo = parrafo?.firstChild;
  if (!nodo || nodo.nodeType !== Node.TEXT_NODE) return null;
  const r = new Range();
  r.setStart(nodo, Math.min(ini, nodo.textContent!.length));
  r.setEnd(nodo, Math.min(fin, nodo.textContent!.length));
  return r;
}

export function pintar(nombre: string, rangos: (Range | null)[]) {
  if (!hayResaltado) return;
  const validos = rangos.filter((r): r is Range => !!r);
  if (validos.length) CSS.highlights.set(nombre, new Highlight(...validos));
  else CSS.highlights.delete(nombre);
}

/** Índice de la palabra bajo el puntero dentro de un párrafo, o null. */
export function palabraEnPunto(parrafo: HTMLElement, x: number, y: number): number | null {
  let nodo: Node | null = null;
  let offset = 0;
  const doc = document as Document & { caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null };
  if (doc.caretPositionFromPoint) {
    const p = doc.caretPositionFromPoint(x, y);
    if (p) [nodo, offset] = [p.offsetNode, p.offset];
  } else if (document.caretRangeFromPoint) {
    const r = document.caretRangeFromPoint(x, y);
    if (r) [nodo, offset] = [r.startContainer, r.startOffset];
  }
  if (!nodo || nodo !== parrafo.firstChild) return null;
  const pos = posiciones(parrafo.textContent ?? "");
  const i = pos.findIndex(([a, b]) => offset >= a && offset <= b);
  return i >= 0 ? i : null;
}
