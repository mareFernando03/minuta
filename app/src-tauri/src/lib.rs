//! Minuta: la app vive en la bandeja. Cerrar la ventana la oculta; salir es solo desde la bandeja, y antes de salir
//! se apaga el motor para que cierre la grabación en curso.

mod bandeja;
mod dictado;
mod enlaces;
mod motor;
mod panel;

use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Manager, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

use motor::{Conexion, Motor};

/// Atajo global para pausar o reanudar las grabaciones desde cualquier aplicación.
const ATAJO_PAUSA: &str = "ctrl+alt+shift+p";
/// Atajo global para marcar el momento actual de la reunión.
const ATAJO_MARCA: &str = "ctrl+alt+shift+m";
/// Atajo global para grabar a mano una reunión presencial (ninguna app toma el micrófono).
const ATAJO_GRABAR: &str = "ctrl+alt+shift+g";
/// Argumento con el que Windows lanza Minuta al iniciar sesión: arranca en la bandeja, sin ventana.
const OCULTA: &str = "--oculta";

#[tauri::command]
async fn motor_conexion(motor: tauri::State<'_, Arc<Motor>>) -> Result<Conexion, String> {
    let motor = motor.inner().clone();
    tauri::async_runtime::spawn_blocking(move || motor.conexion(Duration::from_secs(30)))
        .await
        .map_err(|e| e.to_string())?
}

pub(crate) fn mostrar(app: &AppHandle) {
    if let Some(v) = app.get_webview_window("main") {
        let _ = v.unminimize();
        let _ = v.show();
        let _ = v.set_focus();
    }
}

#[tauri::command]
fn abrir_principal(app: AppHandle) {
    if let Some(p) = app.get_webview_window("panel") {
        let _ = p.hide();
    }
    mostrar(&app);
}

pub(crate) fn salir(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        app.state::<Arc<Motor>>().apagar(Duration::from_secs(8));
        app.exit(0);
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let motor = Arc::new(Motor::default());
    tauri::Builder::default()
        // Va primero: una segunda instancia solo trae al frente la ventana de la que ya corre.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| mostrar(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![OCULTA])))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .manage(motor.clone())
        .manage(enlaces::Pendiente::default())
        .plugin(tauri_plugin_deep_link::init())
        .invoke_handler(tauri::generate_handler![motor_conexion, abrir_principal, enlaces::enlace_pendiente])
        .setup(move |app| {
            motor.arrancar(app.handle().clone());
            bandeja::crear(app.handle())?;
            enlaces::configurar(app.handle());

            let marca: Shortcut = ATAJO_MARCA.parse().expect("atajo válido");
            let grabar: Shortcut = ATAJO_GRABAR.parse().expect("atajo válido");
            let pausa: Shortcut = ATAJO_PAUSA.parse().expect("atajo válido");
            app.handle().plugin(
                tauri_plugin_global_shortcut::Builder::new()
                    .with_handler(move |app, atajo, evento| {
                        if evento.state() != ShortcutState::Pressed {
                            return;
                        }
                        if *atajo == marca {
                            bandeja::pedir_en_segundo_plano(app, "/api/grabacion-actual/marca", "{}");
                        } else if *atajo == grabar {
                            bandeja::pedir_en_segundo_plano(app, "/api/grabacion-actual/empezar", "{}");
                        } else if *atajo == pausa {
                            bandeja::alternar_pausa(app);
                        }
                    })
                    .build(),
            )?;
            for atajo in [ATAJO_PAUSA, ATAJO_MARCA, ATAJO_GRABAR] {
                if let Err(e) = app.global_shortcut().register(atajo) {
                    motor::registrar(&format!("no se pudo registrar el atajo {atajo}: {e}"));
                }
            }
            // Ctrl+Win sostenido dicta: va con un hook propio, el plugin de atajos no acepta solo modificadores.
            dictado::iniciar(app.handle());

            // Inicio con Windows activado la primera vez, solo en la versión instalada (en desarrollo apuntaría al
            // ejecutable de target\debug). Después manda lo que se elija en Ajustes.
            if !cfg!(debug_assertions) {
                let marca = app.path().app_data_dir()?.join("inicio-configurado");
                if !marca.exists() {
                    let _ = app.autolaunch().enable();
                    let _ = std::fs::create_dir_all(marca.parent().unwrap());
                    let _ = std::fs::write(&marca, b"");
                }
            }

            if !std::env::args().any(|a| a == OCULTA) {
                mostrar(app.handle());
            }
            Ok(())
        })
        .on_window_event(|ventana, evento| match evento {
            // Cerrar la ventana no cierra Minuta: sigue en la bandeja. Salir es desde el menú del ícono.
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = ventana.hide();
            }
            WindowEvent::Focused(false) if ventana.label() == "panel" => {
                if let Some(p) = ventana.app_handle().get_webview_window("panel") {
                    panel::al_perder_foco(&p);
                }
            }
            WindowEvent::Moved(posicion) if ventana.label() == "panel" => {
                if let Some(p) = ventana.app_handle().get_webview_window("panel") {
                    panel::al_mover(&p, *posicion);
                }
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("error al correr Minuta");
}
