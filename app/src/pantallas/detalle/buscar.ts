// Búsqueda dentro de una reunión, sin distinguir tildes ni mayúsculas («contoso» encuentra «Contosód»). Se
// normaliza carácter por carácter para que las posiciones coincidan con el texto original y se puedan resaltar.

import type { Segmento } from "../../api/tipos";

export type Coincidencia = [turno: number, inicio: number, fin: number];

function normalizar(texto: string) {
  let salida = "";
  for (const c of texto) {
    const n = c.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
    salida += (n[0] ?? c).padEnd(c.length, " "); // misma longitud en unidades UTF-16 que el original
  }
  return salida;
}

/** Textos normalizados una sola vez por reunión: normalizar ~90 mil caracteres en cada tecla trababa la escritura. */
export function indexar(segmentos: Segmento[]) {
  return segmentos.map((s) => normalizar(s.texto));
}

export function coincidencias(normalizados: string[], consulta: string): Coincidencia[] {
  const q = normalizar(consulta.trim());
  if (q.length < 2) return [];
  const lista: Coincidencia[] = [];
  normalizados.forEach((texto, i) => {
    for (let desde = texto.indexOf(q); desde >= 0; desde = texto.indexOf(q, desde + q.length)) lista.push([i, desde, desde + q.length]);
  });
  return lista;
}
