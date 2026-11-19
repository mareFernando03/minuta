import { expect, test, type Page } from "@playwright/test";
import { BRIEF, ESPERA, GRABANDO, simularMotor } from "./simulador";

/** Abre la app y va a una sección (por defecto Reuniones, donde arranca). */
async function abrir(page: Page, seccion = "Reuniones") {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Secciones" }).getByRole("button", { name: seccion, exact: true }).click();
}

test("arranca en Reuniones", async ({ page }) => {
  await simularMotor(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Reuniones" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Secciones" }).getByRole("button", { name: "Reuniones", exact: true })).toHaveAttribute("aria-current", "page");
});

test("si está grabando al abrir, arranca en Ahora", async ({ page }) => {
  await simularMotor(page, GRABANDO);
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Secciones" }).getByRole("button", { name: /^Ahora/ })).toHaveAttribute("aria-current", "page");
});

test("la biblioteca lista las reuniones y abre el detalle con sus marcas", async ({ page }) => {
  await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  await expect(page.getByRole("heading", { name: "Daily Orbit" })).toBeVisible();
  await expect(page.getByText("Perfecto, entonces lo pasamos a producción el jueves.")).toBeVisible();
  // La marca aparece en la lista y en el margen del turno que sonaba cuando se marcó.
  await expect(page.getByRole("region", { name: "Marcas" }).getByRole("textbox")).toHaveValue("Producción el jueves");
  await expect(page.getByRole("button", { name: /Decisión en 00:42/ })).toBeVisible();
});

test("la búsqueda muestra el fragmento y abre la reunión", async ({ page }) => {
  await simularMotor(page);
  await abrir(page);
  const respuesta = page.waitForResponse(/api\/buscar/);
  await page.getByPlaceholder(/Buscar en lo que se dijo/).fill("producción");
  await respuesta;
  await expect(page.getByText("producción", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /lo pasamos a/ }).click();
  await expect(page.getByRole("heading", { name: "Daily Orbit" })).toBeVisible();
});

test("renombrar a quien habla manda el nombre al motor", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  await page.getByRole("button", { name: /Ellos/ }).first().click();
  await page.getByRole("textbox", { name: /nombre/i }).fill("Pedro Ríos");
  await page.keyboard.press("Enter");
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ metodo: "PATCH", cuerpo: { hablantes: { sistema: "Pedro Ríos" } } });
});

test("grabando: marcar, cortar acá y las alertas con sus acciones", async ({ page }) => {
  const m = await simularMotor(page, {
    ...GRABANDO,
    alertas: [{ tipo: "silencio", mensaje: "Hace 10 minutos que no se escucha nada. ¿Sigue la reunión?", desde: "", vence: new Date(Date.now() + 90_000).toISOString() }],
  });
  await abrir(page);
  await page.getByRole("button", { name: "Ahora" }).click();
  await expect(page.getByRole("heading", { name: "Refinamiento Northwind" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("¿Sigue la reunión?");
  await expect(page.getByRole("alert")).toContainText(/corta en 1:[23]\d/);

  await page.getByRole("button", { name: "Sigue la reunión" }).click();
  await expect.poll(() => m.pedidos.map((p) => p.ruta)).toContain("/api/grabacion-actual/seguir");

  await page.getByRole("button", { name: "Marcar este momento" }).click();
  await expect(page.getByText("Marcado en 12:34")).toBeVisible();

  await page.getByRole("button", { name: /Cortar acá/ }).click();
  await expect.poll(() => m.pedidos.map((p) => p.ruta)).toContain("/api/grabacion-actual/cortar");
});

test("en espera: grabar a mano una presencial con título y carpeta", async ({ page }) => {
  const m = await simularMotor(page, ESPERA);
  await abrir(page);
  await page.getByRole("button", { name: "Ahora" }).click();
  const form = page.getByRole("form", { name: "Grabar a mano" });
  await form.getByRole("textbox", { name: /Título/ }).fill("Charla con Pedro");
  await form.getByRole("combobox", { name: "Carpeta" }).selectOption("Orbit");
  await form.getByRole("button", { name: "Grabar ahora" }).click();
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({
    metodo: "POST", ruta: "/api/grabacion-actual/empezar", cuerpo: { titulo: "Charla con Pedro", carpeta: "Orbit" },
  });
});

test("grabando a mano: título propio y Terminar", async ({ page }) => {
  const m = await simularMotor(page, { ...GRABANDO, apps: ["manual"], manual: true, titulo: "Charla con Pedro", ventanas: [] });
  await abrir(page);
  await page.getByRole("button", { name: "Ahora" }).click();
  await expect(page.getByRole("heading", { name: "Charla con Pedro" })).toBeVisible();
  await page.getByRole("button", { name: /Terminar/ }).click();
  await expect.poll(() => m.pedidos.map((p) => p.ruta)).toContain("/api/grabacion-actual/cortar");
});

test("en proceso: avance, cuánto falta y reintentar un error", async ({ page }) => {
  const m = await simularMotor(page);
  m.estado.cola = [
    { id: 1, tipo: "transcribir", grabacion_id: "g1", estado: "corriendo", intentos: 1, progreso: "transcribiendo a los demás en AssemblyAI", error: null, actualizado: "", avance: 0.42, restante_s: 170, inicio: "2026-10-10T11:02:00", titulo: null, ventanas: ["Meet - Daily Orbit - Google Chrome"], apps: ["chrome.exe"] },
  ];
  m.estado.errores = [{ grabacion_id: "g3", inicio: "2026-10-10T09:15:00", titulo: null, ventanas: [], apps: ["slack.exe"], error: "HTTP 500" }];
  await abrir(page);
  const proceso = page.getByRole("region", { name: "En proceso" });
  await expect(proceso).toContainText("Transcribiendo a los demás en AssemblyAI · 42 % · faltan ~3 min");
  await expect(proceso.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
  await expect(proceso).toContainText("No se pudo procesar: HTTP 500");
  await proceso.getByRole("button", { name: "Reintentar" }).click();
  await expect.poll(() => m.pedidos.map((p) => p.ruta)).toContain("/api/grabaciones/g3/reprocesar");
});

test("un evento de reunión lista avisa en la app", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page);
  await expect.poll(() => m.eventos()).not.toBeNull();
  m.eventos()!.send(JSON.stringify({ tipo: "reunion_lista", datos: { id: "local-1", titulo: "Daily Orbit", revisar: false }, en: "" }));
  await expect(page.getByText("Lista: «Daily Orbit»")).toBeVisible();
});

test("la paleta de comandos navega con el teclado", async ({ page }) => {
  await simularMotor(page);
  await abrir(page);
  await expect(page.getByRole("heading", { name: "Reuniones" })).toBeVisible(); // los atajos se registran al montar
  await page.keyboard.press("Control+k");
  await page.keyboard.type("Glosario");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Glosario" })).toBeVisible();
});

test("el panel de la bandeja pausa las grabaciones", async ({ page }) => {
  const m = await simularMotor(page, ESPERA);
  await page.goto("/#panel");
  await expect(page.getByText("En espera")).toBeVisible();
  await page.getByRole("button", { name: "Pausar" }).click();
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ ruta: "/api/pausa", cuerpo: { pausado: true } });
});

test("Ctrl+F busca dentro de la reunión sin distinguir tildes", async ({ page }) => {
  await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  await expect(page.getByRole("heading", { name: "Daily Orbit" })).toBeVisible();
  await page.keyboard.press("Control+f");
  const campo = page.getByRole("textbox", { name: "Buscar en esta reunión" });
  await expect(campo).toBeFocused();
  await campo.fill("produccion");
  await expect(page.getByRole("search")).toContainText("1 de 1");
  await campo.fill("el");
  await expect(page.getByRole("search")).toContainText(/1 de \d+/);
  await campo.press("Enter");
  await expect(page.getByRole("search")).toContainText(/2 de \d+/);
  await campo.press("Escape");
  await expect(page.getByRole("search")).toHaveCount(0);
});

test("copiar un turno lo deja con minuto y quién habla, y el enlace abre ese minuto", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  await page.getByText("Perfecto, entonces lo pasamos a producción el jueves.").hover();
  await page.getByRole("button", { name: "Copiar el turno de 00:40" }).click();
  await expect(page.getByText("Turno copiado")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("[00:40] Ellos: Perfecto, entonces lo pasamos a producción el jueves.");
  await page.getByRole("button", { name: "Copiar el enlace a 00:40" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("[Daily Orbit · 00:40](minuta://reunion/local-1?t=40)");
});

test("corregir un turno ofrece cambiar las otras y sumarlo al glosario", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  await page.getByText("Arrancamos con el estado del despliegue de Contosó.").dblclick();
  const editor = page.getByRole("textbox", { name: /Corregir el turno/ });
  await editor.fill("Arrancamos con el estado del despliegue de Contoso.");
  await editor.press("Enter");
  await expect(page.getByText("«Contosó» → «Contoso»")).toBeVisible();
  await page.getByRole("button", { name: "Cambiar también las otras 2" }).click();
  await expect(page.getByText("Se cambiaron 2 más")).toBeVisible();
  expect(m.pedidos.map((p) => p.ruta)).toEqual(expect.arrayContaining(["/api/reuniones/local-1/segmentos/1", "/api/reuniones/local-1/reemplazar"]));
});

test("borrar un turno se puede deshacer y no llega al motor", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  const turno = page.getByText("Yo terminé el script de la base.");
  await turno.hover();
  await page.getByRole("button", { name: "Borrar el turno de 00:10" }).click();
  await expect(turno).toHaveCount(0);
  await expect(page.getByText("Se borra el tramo 00:10–00:14: texto")).toBeVisible();
  await page.getByRole("button", { name: "Deshacer" }).click();
  await expect(page.getByText("Yo terminé el script de la base.")).toBeVisible();
  expect(m.pedidos.filter((p) => p.ruta.endsWith("/borrar-tramo"))).toHaveLength(0);
});

test("seleccionar dos turnos y borrarlos manda el tramo a los 10 s", async ({ page }) => {
  await page.clock.install();
  const m = await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  await expect(page.getByText("Yo terminé el script de la base.")).toBeVisible();
  await page.evaluate(() => {
    const ps = [...document.querySelectorAll("p")].filter((p) => /Arrancamos|Yo terminé/.test(p.textContent ?? ""));
    const r = document.createRange();
    r.setStart(ps[0].firstChild!, 3);
    r.setEnd(ps[1].firstChild!, 6);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(r);
  });
  await page.getByRole("toolbar", { name: "Acciones sobre lo seleccionado" }).getByRole("button", { name: "Borrar estos 2 turnos" }).click();
  await page.clock.fastForward(10_500);
  await expect.poll(() => m.pedidos.find((p) => p.ruta.endsWith("/borrar-tramo"))?.cuerpo).toEqual({ segmentos: [1, 2] });
});

test("Ctrl+/ muestra los atajos y los tooltips dicen el atajo", async ({ page }) => {
  await simularMotor(page);
  await abrir(page);
  await expect(page.getByRole("heading", { name: "Reuniones" })).toBeVisible();
  await page.keyboard.press("Control+/");
  const hoja = page.getByRole("dialog", { name: "Atajos" });
  await expect(hoja).toContainText("Ctrl+Alt+Shift+M");
  await expect(hoja).toContainText("Palabra dudosa siguiente o anterior");
  await page.keyboard.press("Escape");
  await expect(hoja).toBeHidden();
  await page.getByRole("button", { name: /Daily Orbit/ }).first().click();
  await page.getByText("Yo terminé el script de la base.").hover();
  await page.getByRole("button", { name: "Corregir el turno de 00:10" }).focus();
  await expect(page.getByRole("tooltip", { name: /Corregir/ })).toBeVisible();
});

test("las voces de «Ellos»: confirmar una sugerencia y nombrar otra", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Refinamiento Northwind/ }).first().click();
  await expect(page.getByText("¿Es Pedro Ríos?")).toBeVisible();
  await expect(page.getByText("Ellos · Voz 2").first()).toBeVisible(); // varias voces sin nombre: se distinguen
  await page.getByRole("button", { name: "Sí, Voz 1 es Pedro Ríos" }).click();
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ ruta: "/api/reuniones/local-2/voces/V1/nombre", cuerpo: { nombre: "Pedro Ríos" } });
  await page.getByRole("button", { name: /Voz 2/ }).first().click();
  const campo = page.getByRole("combobox", { name: "Quién es Voz 2" });
  await campo.fill("Juan Castro");
  await campo.press("Enter");
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ ruta: "/api/reuniones/local-2/voces/V2/nombre", cuerpo: { nombre: "Juan Castro" } });
});

test("el resumen: citas al minuto, aceptar una acción, regenerar y aplicar lo que propone Claude", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page);
  await page.getByRole("button", { name: /Planning GLOBEX/ }).first().click();
  const resumen = page.getByRole("region", { name: "Resumen" });
  await expect(resumen.getByText("El script de la base pasa a producción el jueves.")).toBeVisible();
  await expect(resumen.getByRole("button", { name: "Escuchar desde 00:40" }).first()).toBeVisible();

  await resumen.getByRole("button", { name: "Aceptar" }).click();
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ metodo: "PATCH", ruta: "/api/acciones/21", cuerpo: { estado: "aceptada" } });

  await resumen.getByRole("button", { name: "Regenerar…" }).click();
  await page.getByRole("textbox", { name: "Qué cambiar del resumen" }).fill("más corto");
  await page.keyboard.press("Enter");
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ ruta: "/api/reuniones/local-3/resumir", cuerpo: { pedido: "más corto" } });
  await expect(resumen.getByText(/Resumiendo con Claude/)).toBeVisible();
  // Si el motor avisa que falló, deja de decir «Resumiendo» y cuenta por qué.
  m.eventos()?.send(JSON.stringify({ tipo: "resumen", datos: { id: "local-3", error: "No se pudo resumir con Claude: timeout" }, en: "" }));
  await expect(page.getByText("No se pudo resumir con Claude: timeout")).toBeVisible();
  await expect(resumen.getByText(/Resumiendo con Claude/)).toBeHidden();

  await page.getByRole("button", { name: /Claude propone 1 corrección/ }).click();
  await expect(page.getByRole("region", { name: "Correcciones propuestas" }).locator("ins")).toHaveText("Contoso.");
  await page.getByRole("button", { name: "Aplicar" }).click();
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ metodo: "POST", ruta: "/api/ediciones/5/aplicar" });
});

test("en espera, el brief de la próxima reunión trae lo que quedó de la anterior", async ({ page }) => {
  await simularMotor(page, ESPERA, BRIEF);
  await abrir(page);
  await page.getByRole("button", { name: "Ahora" }).click();
  const brief = page.getByRole("region", { name: "Tu próxima reunión" });
  await expect(brief.getByText(/Empieza en \d+ min/)).toBeVisible();
  await expect(brief.getByText("Producción el jueves")).toBeVisible();
  await expect(brief.getByText(/Mandar el script/)).toBeVisible();
  await brief.getByRole("button", { name: "Daily Orbit" }).click();
  await expect(page.getByRole("heading", { name: "Daily Orbit", level: 1 })).toBeVisible();
});

test("grabando: una nota queda con su minuto", async ({ page }) => {
  const m = await simularMotor(page, GRABANDO);
  await abrir(page);
  await page.getByRole("button", { name: "Ahora" }).click();
  const nota = page.getByRole("textbox", { name: "Nota de este momento" });
  await nota.fill("Preguntar por el contrato");
  await nota.press("Enter");
  await expect.poll(() => m.pedidos.at(-1)).toMatchObject({ ruta: "/api/grabacion-actual/marca", cuerpo: { nota: "Preguntar por el contrato" } });
  await expect(nota).toHaveValue("");
  await expect(page.getByText("Anotado en 12:34")).toBeVisible();
});

test("Ctrl+K busca en lo que se dijo y abre la reunión en ese minuto", async ({ page }) => {
  await simularMotor(page);
  await abrir(page);
  await expect(page.getByRole("heading", { name: "Reuniones" })).toBeVisible();
  await page.keyboard.press("Control+k");
  // Busca mientras se escribe: la última palabra a medias ya encuentra.
  const pedido = page.waitForRequest(/api\/buscar\?/);
  await page.keyboard.type("produc");
  expect(new URL((await pedido).url()).searchParams.get("prefijo")).toBe("true");
  const opcion = page.getByRole("option", { name: /Daily Orbit/ });
  await expect(opcion).toContainText("00:40");
  await opcion.hover();
  await expect(page.getByText("Enter abre la reunión en 00:40.")).toBeVisible();
  await opcion.click();
  await expect(page.getByRole("heading", { name: "Daily Orbit" })).toBeVisible();
});

test("Ctrl+K: Enter apretado enseguida abre el resultado de lo escrito", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page);
  await expect(page.getByRole("heading", { name: "Reuniones" })).toBeVisible();
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox").fill("producción");
  await page.keyboard.press("Enter"); // antes de que llegue la búsqueda
  await expect(page.getByRole("heading", { name: "Daily Orbit" })).toBeVisible();
  expect(m.pedidos.filter((p) => p.ruta === "/api/pausa")).toHaveLength(0); // no se ejecutó una acción
});

test("el dictado se enciende desde Ajustes y avisa que el audio va a Groq", async ({ page }) => {
  const m = await simularMotor(page);
  await abrir(page, "Ajustes");
  await expect(page.getByText(/Con Groq el audio sale de esta PC/)).toBeVisible();
  // El motor simulado responde siempre apagado: solo importa lo que se le pidió.
  await page.getByRole("checkbox", { name: /Dictar con Ctrl\+Win sostenido/ }).click();
  await expect.poll(() => m.pedidos.find((p) => p.ruta === "/api/ajustes")?.cuerpo).toEqual({ dictado: true });
});

test("la píldora del dictado sigue lo que avisa el motor", async ({ page }) => {
  const m = await simularMotor(page);
  await page.goto("/#dictado");
  await expect(page.getByRole("meter", { name: "Nivel del micrófono" })).toBeVisible();
  await expect.poll(() => m.eventos()).not.toBeNull();
  m.eventos()!.send(JSON.stringify({ tipo: "dictado", datos: { estado: "transcribiendo" }, en: "" }));
  await expect(page.getByText("Transcribiendo…")).toBeVisible();
  m.eventos()!.send(JSON.stringify({ tipo: "dictado", datos: { estado: "error", mensaje: "No se pudo transcribir: red" }, en: "" }));
  await expect(page.getByText("No se pudo transcribir: red")).toBeVisible();
  m.eventos()!.send(JSON.stringify({ tipo: "dictado", datos: { estado: "listo", vacio: true }, en: "" }));
  await expect(page.getByText("No te escuché")).toBeVisible();
  // El dictado siguiente arranca de cero: vuelve el medidor, sin el aviso anterior.
  m.eventos()!.send(JSON.stringify({ tipo: "dictado", datos: { estado: "escuchando", nivel: 0.05 }, en: "" }));
  await expect(page.getByRole("meter", { name: "Nivel del micrófono" })).toBeVisible();
  await expect(page.getByText("No te escuché")).toBeHidden();
});
