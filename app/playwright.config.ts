// Tests de punta a punta de la interfaz, con la API del motor simulada (e2e/simulador.ts).
// Corre contra un Vite propio en el 1430 que apunta a un motor inexistente en el 8799: si un test se olvida de
// simular una ruta, falla en vez de tocar un motor real. Usa el Chrome instalado (no baja navegadores).

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 20_000,
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:1430",
    channel: "chrome",
    viewport: { width: 1280, height: 800 },
    locale: "es-AR",
    timezoneId: "America/Argentina/Buenos_Aires",
  },
  webServer: {
    command: "npx vite --port 1430 --strictPort",
    url: "http://localhost:1430",
    reuseExistingServer: false,
    env: { VITE_MOTOR_PUERTO: "8799" },
  },
});
