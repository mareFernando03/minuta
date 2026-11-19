//! Ícono de la bandeja: refleja el estado del motor y da las acciones comunes sin abrir la ventana.
//!
//! Clic izquierdo abre el panel (estado y tareas comunes); clic derecho, el menú (Abrir, Pausar o reanudar, Salir). El estado se lee de la API
//! cada 2 s desde Rust, así la bandeja sigue al día aunque la ventana esté cerrada.

use std::sync::Arc;
use std::thread;
use std::time::Duration;

use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::window::{ProgressBarState, ProgressBarStatus};
use tauri::{AppHandle, Manager, Wry};

use crate::motor::Motor;

const ID: &str = "principal";

#[derive(Clone, Copy, PartialEq)]
enum Estado {
    Espera,
    Grabando,
    Revisar,
    Pausa,
    SinMotor,
}

impl Estado {
    fn icono(self) -> Image<'static> {
        let bytes: &[u8] = match self {
            Estado::Espera => include_bytes!("../icons/bandeja/espera.png"),
            Estado::Grabando => include_bytes!("../icons/bandeja/grabando.png"),
            Estado::Revisar => include_bytes!("../icons/bandeja/revisar.png"),
            Estado::Pausa | Estado::SinMotor => include_bytes!("../icons/bandeja/pausa.png"),
        };
        Image::from_bytes(bytes).expect("ícono de bandeja válido")
    }
}

pub struct Bandeja {
    pausar: MenuItem<Wry>,
    grabar: MenuItem<Wry>,
    cortar: MenuItem<Wry>,
}

pub fn crear(app: &AppHandle) -> tauri::Result<()> {
    let abrir = MenuItem::with_id(app, "abrir", "Abrir Minuta", true, None::<&str>)?;
    let pausar = MenuItem::with_id(app, "pausar", "Pausar las grabaciones", true, None::<&str>)?;
    let grabar = MenuItem::with_id(app, "grabar", "Grabar a mano (presencial)", true, None::<&str>)?;
    let cortar = MenuItem::with_id(app, "cortar", "Cortar la grabación acá", false, None::<&str>)?;
    let ultimo = MenuItem::with_id(app, "ultimo_dictado", "Copiar el último dictado", true, None::<&str>)?;
    let salir = MenuItem::with_id(app, "salir", "Salir de Minuta", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[&abrir, &pausar, &grabar, &cortar, &ultimo, &PredefinedMenuItem::separator(app)?, &salir],
    )?;

    TrayIconBuilder::with_id(ID)
        .icon(Estado::SinMotor.icono())
        .tooltip("Minuta: arrancando")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, evento| match evento.id().as_ref() {
            "abrir" => crate::mostrar(app),
            "pausar" => alternar_pausa(app),
            "grabar" => pedir_en_segundo_plano(app, "/api/grabacion-actual/empezar", "{}"),
            "cortar" => pedir_en_segundo_plano(app, "/api/grabacion-actual/cortar", "{}"),
            "ultimo_dictado" => crate::dictado::copiar_el_ultimo(app),
            "salir" => crate::salir(app),
            _ => {}
        })
        .on_tray_icon_event(|icono, evento| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, position, .. } = evento {
                crate::panel::alternar(icono.app_handle(), position);
            }
        })
        .build(app)?;

    app.manage(Bandeja { pausar, grabar, cortar });
    let app = app.clone();
    thread::Builder::new().name("bandeja".into()).spawn(move || seguir_estado(app))?;
    Ok(())
}

fn seguir_estado(app: AppHandle) {
    let motor = app.state::<Arc<Motor>>().inner().clone();
    let mut anterior: Option<(Estado, bool)> = None;
    let mut grababa = false;
    let mut barra_anterior = None;
    loop {
        let respuesta = motor.pedir("GET", "/api/estado", None);
        let avance = respuesta.as_ref().ok().and_then(avance_de_la_cola);
        let (estado, pausado, detalle) = match respuesta {
            Ok(e) => {
                let ahora = &e["ahora"];
                let pausado = ahora["pausado"].as_bool().unwrap_or(false);
                let revisar = e["para_revisar"].as_u64().unwrap_or(0);
                if ahora["grabando"].as_bool().unwrap_or(false) {
                    let min = ahora["segundos"].as_u64().unwrap_or(0) / 60;
                    (Estado::Grabando, pausado, format!("grabando, {min} min"))
                } else if pausado {
                    (Estado::Pausa, true, "en pausa".into())
                } else if revisar > 0 {
                    let s = if revisar == 1 { "" } else { "es" };
                    (Estado::Revisar, false, format!("{revisar} grabación{s} para revisar"))
                } else {
                    (Estado::Espera, false, "en espera".into())
                }
            }
            Err(_) => (Estado::SinMotor, false, "el motor se está reiniciando".into()),
        };
        if let Some(icono) = app.tray_by_id(ID) {
            if anterior.map(|a| a.0) != Some(estado) {
                let _ = icono.set_icon(Some(estado.icono()));
            }
            let _ = icono.set_tooltip(Some(format!("Minuta: {detalle}")));
        }
        if anterior.map(|a| a.1) != Some(pausado) {
            let texto = if pausado { "Reanudar las grabaciones" } else { "Pausar las grabaciones" };
            let _ = app.state::<Bandeja>().pausar.set_text(texto);
        }
        let graba = estado == Estado::Grabando;
        if graba != grababa {
            let _ = app.state::<Bandeja>().cortar.set_enabled(graba);
            let _ = app.state::<Bandeja>().grabar.set_enabled(!graba);
            grababa = graba;
        }
        if let Some(v) = app.get_webview_window("main") {
            // Al ocultar y mostrar la ventana, Windows recrea su botón: se vuelve a aplicar.
            let clave = (graba, avance, v.is_visible().unwrap_or(false));
            if barra_anterior != Some(clave) {
                barra_de_tareas(&v, graba, avance);
                barra_anterior = Some(clave);
            }
        }
        anterior = Some((estado, pausado));
        thread::sleep(Duration::from_secs(2));
    }
}

/// Mayor avance (en %) entre los trabajos que corren, si hay alguno.
fn avance_de_la_cola(estado: &serde_json::Value) -> Option<u64> {
    estado["cola"]
        .as_array()?
        .iter()
        .filter(|t| t["estado"] == "corriendo")
        .filter_map(|t| t["avance"].as_f64())
        .map(|a| (a * 100.0).round().clamp(1.0, 100.0) as u64)
        .max()
}

/// El botón de la barra de tareas también avisa, porque Windows 11 manda los íconos de la bandeja a los ocultos:
/// punto bermellón mientras graba y barra de progreso mientras transcribe.
fn barra_de_tareas(v: &tauri::WebviewWindow, graba: bool, avance: Option<u64>) {
    let punto = graba.then(|| Image::from_bytes(include_bytes!("../icons/bandeja/punto.png")).expect("punto válido"));
    let _ = v.set_overlay_icon(punto);
    let status = if avance.is_some() { ProgressBarStatus::Normal } else { ProgressBarStatus::None };
    let _ = v.set_progress_bar(ProgressBarState { status: Some(status), progress: avance });
}

/// POST a la API sin bloquear el hilo principal (los menús y atajos corren en él).
pub fn pedir_en_segundo_plano(app: &AppHandle, ruta: &'static str, cuerpo: &'static str) {
    let motor = app.state::<Arc<Motor>>().inner().clone();
    thread::spawn(move || {
        if let Err(e) = motor.pedir("POST", ruta, Some(cuerpo)) {
            crate::motor::registrar(&format!("POST {ruta}: {e}"));
        }
    });
}

/// Lo usan el menú y el atajo global. Corre fuera del hilo principal porque habla por HTTP.
pub fn alternar_pausa(app: &AppHandle) {
    let motor = app.state::<Arc<Motor>>().inner().clone();
    thread::spawn(move || {
        let pausado = motor
            .pedir("GET", "/api/estado", None)
            .map(|e| e["ahora"]["pausado"].as_bool().unwrap_or(false))
            .unwrap_or(false);
        let cuerpo = format!("{{\"pausado\": {}}}", !pausado);
        if let Err(e) = motor.pedir("POST", "/api/pausa", Some(&cuerpo)) {
            crate::motor::registrar(&format!("no se pudo cambiar la pausa: {e}"));
        }
    });
}
