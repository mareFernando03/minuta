// Preferencias de la interfaz que se recuerdan entre sesiones (barra achicada, grupos plegados, pestaña elegida).
// Si el almacenamiento no está disponible, la preferencia dura lo que dura la ventana.

export function leerPreferencia(clave: string): string | null {
  try {
    return localStorage.getItem(clave);
  } catch {
    return null;
  }
}

export function guardarPreferencia(clave: string, valor: string) {
  try {
    localStorage.setItem(clave, valor);
  } catch {
    /* sin almacenamiento: la preferencia dura la sesión */
  }
}
