//! Panel de la bandeja: ventana chica sin marco que abre el clic izquierdo en el ícono y se oculta al perder el foco.
//!
//! La primera vez aparece sobre la barra de tareas, centrado en el clic. Se arrastra desde su franja superior y, si
//! el usuario lo mueve, recuerda ese lugar (también entre reinicios, en `panel.json`). Si ese lugar ya no cae en
//! ningún monitor, vuelve junto al ícono.

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, PhysicalPosition, WebviewWindow};

/// Cuándo se ocultó por perder el foco. Al hacer clic en el ícono con el panel abierto, Windows primero le quita el
/// foco (se oculta) y después llega el clic: sin esta marca, el mismo clic lo volvería a abrir.
static OCULTO_EN: Mutex<Option<Instant>> = Mutex::new(None);
/// La última posición que puso la app. Windows avisa también esos movimientos, y no hay que guardarlos como elección.
static PUESTA: Mutex<Option<PhysicalPosition<i32>>> = Mutex::new(None);
/// Lugar elegido por el usuario y todavía no escrito en disco (se escribe al ocultarse, no en cada paso del arrastre).
static MOVIDA: Mutex<Option<PhysicalPosition<i32>>> = Mutex::new(None);

pub fn alternar(app: &AppHandle, clic: PhysicalPosition<f64>) {
    let Some(panel) = app.get_webview_window("panel") else {
        return crate::mostrar(app);
    };
    let recien_oculto = OCULTO_EN.lock().unwrap().is_some_and(|t| t.elapsed() < Duration::from_millis(300));
    if panel.is_visible().unwrap_or(false) || recien_oculto {
        let _ = panel.hide();
        return;
    }
    let lugar = elegido(app).filter(|p| en_algun_monitor(&panel, *p)).or_else(|| junto_al_icono(&panel, clic));
    if let Some(p) = lugar {
        *PUESTA.lock().unwrap() = Some(p);
        let _ = panel.set_position(p);
    }
    let _ = panel.show();
    let _ = panel.set_focus();
}

pub fn al_mover(panel: &WebviewWindow, p: PhysicalPosition<i32>) {
    if *PUESTA.lock().unwrap() == Some(p) || !panel.is_visible().unwrap_or(false) {
        return;
    }
    *PUESTA.lock().unwrap() = Some(p);
    *MOVIDA.lock().unwrap() = Some(p);
}

/// Al empezar a arrastrar, el foco pasa un instante del WebView a la ventana y Windows avisa `Focused(false)` seguido
/// de `Focused(true)`. Si se ocultara con el primer aviso, el panel desaparecería al intentar moverlo: se espera un
/// momento y se oculta solo si de verdad sigue sin foco.
pub fn al_perder_foco(panel: &WebviewWindow) {
    let panel = panel.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(150));
        if !panel.is_focused().unwrap_or(false) {
            ocultar(&panel);
        }
    });
}

fn ocultar(panel: &WebviewWindow) {
    *OCULTO_EN.lock().unwrap() = Some(Instant::now());
    let _ = panel.hide();
    if let (Some(p), Some(ruta)) = (MOVIDA.lock().unwrap().take(), archivo(panel.app_handle())) {
        let _ = fs::create_dir_all(ruta.parent().unwrap());
        let _ = fs::write(ruta, serde_json::json!({ "x": p.x, "y": p.y }).to_string());
    }
}

fn junto_al_icono(panel: &WebviewWindow, clic: PhysicalPosition<f64>) -> Option<PhysicalPosition<i32>> {
    let (tam, monitor) = (panel.outer_size().ok()?, panel.monitor_from_point(clic.x, clic.y).ok()??);
    let area = monitor.work_area();
    let margen = (8.0 * monitor.scale_factor()) as i32;
    let (ancho, alto) = (tam.width as i32, tam.height as i32);
    let derecha = area.position.x + area.size.width as i32;
    let abajo = area.position.y + area.size.height as i32;
    let x = (clic.x as i32 - ancho / 2).clamp(area.position.x + margen, derecha - ancho - margen);
    Some(PhysicalPosition::new(x, abajo - alto - margen))
}

/// Alcanza con que la franja superior (de donde se arrastra) quede en algún monitor.
fn en_algun_monitor(panel: &WebviewWindow, p: PhysicalPosition<i32>) -> bool {
    matches!(panel.monitor_from_point(p.x as f64 + 40.0, p.y as f64 + 16.0), Ok(Some(_)))
}

fn elegido(app: &AppHandle) -> Option<PhysicalPosition<i32>> {
    let v: serde_json::Value = serde_json::from_str(&fs::read_to_string(archivo(app)?).ok()?).ok()?;
    Some(PhysicalPosition::new(v["x"].as_i64()? as i32, v["y"].as_i64()? as i32))
}

fn archivo(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|d| d.join("panel.json"))
}
