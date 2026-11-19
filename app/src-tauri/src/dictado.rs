//! Dictado con Ctrl+Win sostenido, como en Wispr Flow: mientras se mantiene, el motor escucha el micrófono; al
//! soltar, transcribe y acá se pega el texto donde está el cursor (motor/minuta/dictado.py).
//!
//! El plugin de atajos de Tauri no acepta solo modificadores, así que hay un hook de teclado propio
//! (`WH_KEYBOARD_LL`). Mira solo Ctrl y Win: de cualquier otra tecla solo registra que se apretó, para cancelar,
//! y nunca guarda cuál fue. Si con Ctrl+Win abajo se aprieta otra tecla (Ctrl+Win+D, flechas de escritorios), era
//! un atajo de Windows y el dictado se cancela; por eso recién empieza a los 150 ms. Al soltar Win después de un
//! Ctrl+Win se manda la tecla máscara 0xE8 (sin asignar) para que Windows no abra Inicio, el truco de AutoHotkey.
//!
//! El callback del hook tiene que volver enseguida (Windows lo quita en silencio si tarda): solo cambia el estado y
//! avisa por un canal; los pedidos al motor y el pegado corren en un hilo aparte. El hook está siempre; si el dictado
//! está apagado en Ajustes, el motor responde 409 y no pasa nada.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager, PhysicalPosition};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::System::DataExchange::{IsClipboardFormatAvailable, RegisterClipboardFormatW};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, SendInput, VK_CONTROL, VK_LCONTROL,
    VK_LWIN, VK_RCONTROL, VK_RWIN, VK_V,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetForegroundWindow, GetMessageW, HC_ACTION, HWND_TOPMOST, KBDLLHOOKSTRUCT, LLKHF_INJECTED, MSG,
    SW_HIDE, SW_SHOWNOACTIVATE, SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SetWindowPos, SetWindowsHookExW, ShowWindow,
    WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN,
};

use crate::motor::{registrar, Motor};

/// Cuánto se espera con Ctrl+Win abajo antes de empezar: los atajos de Windows llegan antes.
const ARRANQUE: Duration = Duration::from_millis(150);
/// Groq responde en menos de un segundo; sin red, el motor cae a Whisper local, que tarda más.
const ESPERA_TRANSCRIPCION: Duration = Duration::from_secs(90);
/// Hasta cuándo se espera que se suelten Ctrl y Win antes de pegar: con Win abajo, Ctrl+V sería Win+Ctrl+V.
const ESPERA_SOLTAR: Duration = Duration::from_secs(2);
/// Cuánto se le da a la app de destino para leer el portapapeles después de Ctrl+V, antes de devolver lo que había.
/// Slack, Teams o un escritorio remoto cargados pueden tardar más de medio segundo y pegarían lo anterior.
const ESPERA_PEGADO: Duration = Duration::from_millis(1500);
/// Cada cuánto se confirma, mientras se dicta, que Ctrl y Win siguen físicamente abajo.
const REVISAR_TECLAS: Duration = Duration::from_millis(100);
/// Cuánto queda visible la píldora con el error de transcripción antes de ocultarse.
const ESPERA_ERROR: Duration = Duration::from_secs(3);
/// Cuánto queda «No te escuché» cuando no hubo voz: lo justo para leerlo.
const ESPERA_VACIO: Duration = Duration::from_millis(1200);
/// La tecla máscara: un virtual key sin asignar, para que soltar Win no abra Inicio.
const VK_MASCARA: u16 = 0xE8;
/// Marca de lo que manda Minuta (dwExtraInfo), por si otro programa también inyecta teclas.
const MARCA: usize = 0x4D49_4E55; // "MINU"
/// Lo que mide la píldora y cuánto queda sobre el borde de abajo del monitor.
const PILDORA_ALTO_SOBRE_BORDE: i32 = 96;

enum Evento {
    Acorde,
    OtraTecla,
    Soltar,
}

static CANAL: OnceLock<mpsc::Sender<Evento>> = OnceLock::new();
static CTRL: AtomicBool = AtomicBool::new(false);
static WIN: AtomicBool = AtomicBool::new(false);
/// Ctrl+Win abajo y sin otra tecla en el medio.
static ACORDE: AtomicBool = AtomicBool::new(false);
/// Hubo un Ctrl+Win desde que bajó Win: al soltarlo hay que enmascarar Inicio.
static VISTO: AtomicBool = AtomicBool::new(false);
/// Cuántas veces se mostró la píldora: un ocultado diferido no esconde la de un dictado que empezó después.
static MOSTRADA: AtomicUsize = AtomicUsize::new(0);

pub fn iniciar(app: &AppHandle) {
    let (tx, rx) = mpsc::channel();
    if CANAL.set(tx).is_err() {
        return;
    }
    thread::Builder::new()
        .name("dictado-teclado".into())
        .spawn(escuchar_teclado)
        .expect("hilo del teclado");
    let app = app.clone();
    thread::Builder::new()
        .name("dictado".into())
        .spawn(move || trabajar(app, rx))
        .expect("hilo del dictado");
}

fn avisar(evento: Evento) {
    if let Some(tx) = CANAL.get() {
        let _ = tx.send(evento);
    }
}

fn escuchar_teclado() {
    unsafe {
        let gancho = SetWindowsHookExW(WH_KEYBOARD_LL, Some(al_teclear), GetModuleHandleW(std::ptr::null()), 0);
        if gancho.is_null() {
            registrar("dictado: no se pudo instalar el hook de teclado");
            return;
        }
        // El hook de bajo nivel corre en el hilo que lo instaló, mientras ese hilo atienda mensajes.
        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {}
    }
}

unsafe extern "system" fn al_teclear(codigo: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if codigo == HC_ACTION as i32 {
        let tecla = unsafe { &*(lparam as *const KBDLLHOOKSTRUCT) };
        if tecla.flags & LLKHF_INJECTED == 0 {
            let abajo = matches!(wparam as u32, WM_KEYDOWN | WM_SYSKEYDOWN);
            match tecla.vkCode as u16 {
                VK_LCONTROL | VK_RCONTROL => modificador(&CTRL, abajo),
                VK_LWIN | VK_RWIN => {
                    if !abajo && VISTO.swap(false, Ordering::SeqCst) {
                        enmascarar_inicio();
                    }
                    modificador(&WIN, abajo);
                }
                _ => {
                    if abajo && ACORDE.swap(false, Ordering::SeqCst) {
                        avisar(Evento::OtraTecla);
                    }
                }
            }
        }
    }
    unsafe { CallNextHookEx(std::ptr::null_mut(), codigo, wparam, lparam) }
}

/// La repetición de teclado manda "abajo" una y otra vez mientras se mantiene: solo cuentan los cambios.
fn modificador(cual: &AtomicBool, abajo: bool) {
    let antes = cual.swap(abajo, Ordering::SeqCst);
    if abajo && !antes && CTRL.load(Ordering::SeqCst) && WIN.load(Ordering::SeqCst) {
        ACORDE.store(true, Ordering::SeqCst);
        VISTO.store(true, Ordering::SeqCst);
        avisar(Evento::Acorde);
    } else if !abajo && antes && ACORDE.swap(false, Ordering::SeqCst) {
        avisar(Evento::Soltar);
    }
}

fn tecla(vk: u16, arriba: bool) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk,
                wScan: 0,
                dwFlags: if arriba { KEYEVENTF_KEYUP } else { 0 },
                time: 0,
                dwExtraInfo: MARCA,
            },
        },
    }
}

fn mandar(teclas: &[INPUT]) {
    unsafe { SendInput(teclas.len() as u32, teclas.as_ptr(), size_of::<INPUT>() as i32) };
}

fn enmascarar_inicio() {
    mandar(&[tecla(VK_MASCARA, false), tecla(VK_MASCARA, true)]);
}

/// Si Ctrl y Win siguen físicamente abajo. El hook no ve lo que se suelta en el escritorio seguro (Win+L,
/// Ctrl+Alt+Supr, UAC): sin este chequeo una tecla quedaría "abajo" y dispararía dictados que nadie pidió.
fn fisico(vks: &[u16]) -> bool {
    vks.iter().any(|&vk| unsafe { GetAsyncKeyState(vk as i32) } as u16 & 0x8000 != 0)
}

fn resincronizar() -> bool {
    let (c, w) = (fisico(&[VK_LCONTROL, VK_RCONTROL]), fisico(&[VK_LWIN, VK_RWIN]));
    CTRL.store(c, Ordering::SeqCst);
    WIN.store(w, Ordering::SeqCst);
    c && w
}

fn teclas_abajo() -> bool {
    fisico(&[VK_LCONTROL, VK_RCONTROL, VK_LWIN, VK_RWIN])
}

fn trabajar(app: AppHandle, rx: mpsc::Receiver<Evento>) {
    let motor = app.state::<Arc<Motor>>().inner().clone();
    loop {
        match rx.recv() {
            Ok(Evento::Acorde) => {}
            Ok(_) => continue,
            Err(_) => return,
        }
        match rx.recv_timeout(ARRANQUE) {
            Ok(_) => continue, // soltó enseguida u otra tecla: era un atajo de Windows, no un dictado
            Err(RecvTimeoutError::Disconnected) => return,
            Err(RecvTimeoutError::Timeout) => {}
        }
        if !resincronizar() {
            ACORDE.store(false, Ordering::SeqCst); // una tecla se soltó donde el hook no la vio
            continue;
        }
        // 409 es dictado apagado (o ya escuchando): no se registra, pasaría con cada Ctrl+Win.
        match motor.pedir("POST", "/api/dictado/empezar", Some("{}")) {
            Ok(_) => {}
            Err(e) => {
                if !e.contains("409") {
                    registrar(&format!("dictado: no se pudo empezar: {e}"));
                    // Un timeout no dice si el motor empezó: si empezó, quedaría escuchando y respondiendo 409.
                    let _ = motor.pedir("POST", "/api/dictado/cancelar", Some("{}"));
                }
                continue;
            }
        }
        // Donde se empezó a dictar es donde va el texto; si al terminar el foco está en otra ventana, no se pega.
        let destino = unsafe { GetForegroundWindow() };
        mostrar_pildora(&app);
        let fin = loop {
            match rx.recv_timeout(REVISAR_TECLAS) {
                Ok(Evento::Acorde) => continue,
                Ok(otro) => break otro,
                Err(RecvTimeoutError::Timeout) if !resincronizar() => {
                    // El Soltar del hook puede llegar justo después del estado físico.
                    if let Ok(e @ (Evento::Soltar | Evento::OtraTecla)) = rx.try_recv() {
                        break e;
                    }
                    ACORDE.store(false, Ordering::SeqCst);
                    break Evento::Soltar;
                }
                Err(RecvTimeoutError::Timeout) => continue,
                Err(RecvTimeoutError::Disconnected) => return,
            }
        };
        if matches!(fin, Evento::OtraTecla) {
            let _ = motor.pedir("POST", "/api/dictado/cancelar", Some("{}"));
            ocultar_pildora(&app);
            continue;
        }
        let texto = motor
            .pedir_con_espera("POST", "/api/dictado/terminar", Some("{}"), ESPERA_TRANSCRIPCION)
            .map(|r| r["texto"].as_str().unwrap_or("").to_string());
        match texto {
            Ok(t) if !t.is_empty() => match pegar(&app, &t, destino) {
                Ok(()) => {}
                Err(SinPegar::OtraVentana) => {
                    aviso_en_pildora(&app, "Quedó en el portapapeles: pegalo con Ctrl+V");
                    ocultar_despues(&app, ESPERA_ERROR);
                }
                Err(SinPegar::Portapapeles(e)) => {
                    registrar(&format!("dictado: no se pudo pegar: {e}"));
                    aviso_en_pildora(&app, "No se pudo pegar: está en «Copiar el último dictado»");
                    ocultar_despues(&app, ESPERA_ERROR);
                }
            },
            Ok(_) => ocultar_despues(&app, ESPERA_VACIO), // la píldora dice que no escuchó nada
            Err(e) => {
                registrar(&format!("dictado: no se pudo transcribir: {e}"));
                ocultar_despues(&app, ESPERA_ERROR); // que se lea el error que publicó el motor
            }
        }
    }
}

fn poner(p: &mut arboard::Clipboard, texto: &str) -> Result<(), arboard::Error> {
    use arboard::SetExtWindows;
    p.set().exclude_from_history().exclude_from_cloud().exclude_from_monitoring().text(texto)
}

/// Pone el texto en el portapapeles, manda Ctrl+V y devuelve lo que había. Lo dictado no entra al historial del
/// portapapeles (Win+V), ni a la nube, ni a gestores como Ditto; "Copiar el último dictado" de la bandeja lo
/// recupera si hizo falta.
enum SinPegar {
    /// Cambió la ventana o siguen las teclas abajo: el dictado quedó en el portapapeles.
    OtraVentana,
    Portapapeles(String),
}

fn pegar(app: &AppHandle, texto: &str, destino: HWND) -> Result<(), SinPegar> {
    let falla = |e: arboard::Error| SinPegar::Portapapeles(e.to_string());
    let limite = Instant::now() + ESPERA_SOLTAR;
    while teclas_abajo() && Instant::now() < limite {
        thread::sleep(Duration::from_millis(20));
    }
    let mut portapapeles = arboard::Clipboard::new().map_err(falla)?;
    // Con Win abajo, Ctrl+V sería Win+Ctrl+V; en otra ventana, el texto caería donde no se dictó. No se pega a ciegas.
    if teclas_abajo() || unsafe { GetForegroundWindow() } != destino {
        poner(&mut portapapeles, texto).map_err(falla)?;
        return Err(SinPegar::OtraVentana);
    }
    // Lo que puso un gestor de contraseñas no se lee ni se devuelve: devolverlo lo publicaría sin sus marcas.
    let anterior = if es_sensible() { Anterior::NoTocar } else { Anterior::leer(&mut portapapeles) };
    poner(&mut portapapeles, texto).map_err(falla)?;
    mandar(&[tecla(VK_CONTROL, false), tecla(VK_V, false), tecla(VK_V, true), tecla(VK_CONTROL, true)]);
    ocultar_pildora(app); // ya está pegado: lo que sigue (devolver el portapapeles) no se tiene que ver
    thread::sleep(ESPERA_PEGADO);
    // Si en el medio se copió otra cosa, esa manda: no se pisa.
    if portapapeles.get_text().ok().as_deref() == Some(texto) {
        anterior.devolver(&mut portapapeles);
    }
    Ok(())
}

/// Si lo que hay en el portapapeles lo marcó un gestor de contraseñas como no monitoreable.
fn es_sensible() -> bool {
    let nombre: Vec<u16> = "ExcludeClipboardContentFromMonitorProcessing\0".encode_utf16().collect();
    unsafe {
        let formato = RegisterClipboardFormatW(nombre.as_ptr());
        formato != 0 && IsClipboardFormatAvailable(formato) != 0
    }
}

/// Lo que había en el portapapeles. Texto e imágenes vuelven, sin volver a entrar al historial (ya entraron al
/// copiarse). Lo demás (archivos del Explorador, un rango de Excel con formato) arboard no lo lee: no se toca, y queda
/// el dictado en el portapapeles en lugar de borrarlo.
enum Anterior {
    Texto(String),
    Imagen(arboard::ImageData<'static>),
    NoTocar,
}

impl Anterior {
    fn leer(p: &mut arboard::Clipboard) -> Self {
        match p.get_text() {
            Ok(t) => Anterior::Texto(t),
            // Ocupado por otra app o en otro formato: no se restaura nada, para no borrar lo que había.
            Err(arboard::Error::ContentNotAvailable) => match p.get_image() {
                Ok(i) => Anterior::Imagen(i.to_owned_img()),
                Err(_) => Anterior::NoTocar,
            },
            Err(_) => Anterior::NoTocar,
        }
    }

    fn devolver(self, p: &mut arboard::Clipboard) {
        use arboard::SetExtWindows;
        let _ = match self {
            Anterior::Texto(t) => poner(p, &t),
            Anterior::Imagen(i) => p.set().exclude_from_history().exclude_from_cloud().exclude_from_monitoring().image(i),
            Anterior::NoTocar => Ok(()),
        };
    }
}

/// La copia que pide la bandeja: el último dictado, por si el pegado cayó en otra ventana.
pub fn copiar_el_ultimo(app: &AppHandle) {
    let motor = app.state::<Arc<Motor>>().inner().clone();
    thread::spawn(move || {
        let texto = motor
            .pedir("GET", "/api/dictados?limite=1", None)
            .ok()
            .and_then(|r| r[0]["texto"].as_str().map(str::to_string));
        if let Some(t) = texto {
            if let Err(e) = arboard::Clipboard::new().and_then(|mut p| poner(&mut p, &t)) {
                registrar(&format!("dictado: no se pudo copiar el último: {e}"));
            }
        }
    });
}

/// Abajo al centro del monitor donde está el puntero, sin tomar el foco: si lo tomara, el pegado iría a Minuta.
fn mostrar_pildora(app: &AppHandle) {
    let Some(v) = app.get_webview_window("dictado") else {
        return;
    };
    MOSTRADA.fetch_add(1, Ordering::SeqCst);
    let monitor = app
        .cursor_position()
        .ok()
        .and_then(|c| app.monitor_from_point(c.x, c.y).ok().flatten())
        .or_else(|| app.primary_monitor().ok().flatten());
    if let (Some(m), Ok(tam)) = (monitor, v.outer_size()) {
        let area = m.work_area();
        let x = area.position.x + (area.size.width as i32 - tam.width as i32) / 2;
        let y = area.position.y + area.size.height as i32 - tam.height as i32 - PILDORA_ALTO_SOBRE_BORDE;
        let _ = v.set_position(PhysicalPosition::new(x, y));
    }
    if let Ok(hwnd) = v.hwnd() {
        unsafe {
            // Tauri crea la ventana con barra de título y la saca recién al recalcular el marco, cosa que hace su
            // `show()`. Sin esto, Windows dibujaba «Minuta: dictado» con su ✕ encima de la píldora.
            SetWindowPos(
                hwnd.0 as _,
                std::ptr::null_mut(),
                0,
                0,
                0,
                0,
                SWP_FRAMECHANGED | SWP_NOACTIVATE | SWP_NOZORDER | SWP_NOMOVE | SWP_NOSIZE,
            );
            ShowWindow(hwnd.0 as _, SW_SHOWNOACTIVATE);
            SetWindowPos(hwnd.0 as _, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE);
        }
    }
}

/// Con la misma llamada que la muestra: Tauri no se enteró de que está visible (se mostró con `ShowWindow` para no
/// tomar el foco), y su `hide()` no hacía nada. La píldora quedaba en pantalla con «Transcribiendo…».
fn ocultar_pildora(app: &AppHandle) {
    if let Some(v) = app.get_webview_window("dictado") {
        match v.hwnd() {
            Ok(hwnd) => unsafe {
                ShowWindow(hwnd.0 as _, SW_HIDE);
            },
            Err(_) => {
                let _ = v.hide();
            }
        }
    }
}

/// Un aviso de Rust (lo que el motor no sabe: si se pudo pegar). Lo dibuja la píldora hasta que se oculta.
fn aviso_en_pildora(app: &AppHandle, texto: &str) {
    let _ = app.emit_to("dictado", "dictado-aviso", texto);
}

fn ocultar_despues(app: &AppHandle, espera: Duration) {
    let (vez, app) = (MOSTRADA.load(Ordering::SeqCst), app.clone());
    thread::spawn(move || {
        thread::sleep(espera);
        if MOSTRADA.load(Ordering::SeqCst) == vez {
            ocultar_pildora(&app);
        }
    });
}
