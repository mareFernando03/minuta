//! Enlaces `minuta://reunion/<id>?t=754`: abren Minuta en esa reunión y ese minuto. Se copian desde la
//! transcripción para citarlos en una nota o en un mensaje.
//!
//! El esquema lo registra el instalador. Con la app abierta, la instancia única le pasa el enlace a la que corre;
//! si el enlace la arranca, queda pendiente hasta que la interfaz lo pide (todavía no escuchaba eventos).

use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, Url};
use tauri_plugin_deep_link::DeepLinkExt;

#[derive(Default)]
pub struct Pendiente(Mutex<Option<Value>>);

/// `minuta://reunion/<id>?t=<segundos>` -> {seccion, reunion, t}.
fn destino(url: &Url) -> Option<Value> {
    if url.scheme() != "minuta" || url.host_str() != Some("reunion") {
        return None;
    }
    let id = url.path().trim_start_matches('/');
    if id.is_empty() {
        return None;
    }
    let id = percent_decode(id);
    // Los ids son «local-<uuid>» o uuids de Granola: cualquier otra cosa (barras, «..») no es un enlace de Minuta.
    if id.len() > 100 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return None;
    }
    let t = url
        .query_pairs()
        .find(|(k, _)| k == "t")
        .and_then(|(_, v)| v.parse::<f64>().ok())
        .filter(|t| t.is_finite() && *t >= 0.0);
    Some(json!({ "seccion": "reuniones", "reunion": id, "t": t }))
}

fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut salida = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 3 <= bytes.len() {
            if let Ok(b) = u8::from_str_radix(&s[i + 1..i + 3], 16) {
                salida.push(b);
                i += 3;
                continue;
            }
        }
        salida.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&salida).into_owned()
}

fn abrir(app: &AppHandle, url: &Url, al_arrancar: bool) {
    let Some(d) = destino(url) else {
        crate::motor::registrar(&format!("enlace no reconocido: {url}"));
        return;
    };
    crate::mostrar(app);
    if al_arrancar {
        *app.state::<Pendiente>().0.lock().unwrap() = Some(d);
    } else {
        let _ = app.emit_to("main", "navegar", d);
    }
}

/// `Pendiente` se registra en el builder (lib.rs), no acá: la interfaz pide `enlace_pendiente` al montar, y las
/// ventanas se crean antes de `setup`. Con la píldora del dictado, `setup` llegaba tarde y la app entraba en pánico.
pub fn configurar(app: &AppHandle) {
    let h = app.clone();
    app.deep_link().on_open_url(move |e| {
        for url in e.urls() {
            abrir(&h, &url, false);
        }
    });
    if let Ok(Some(urls)) = app.deep_link().get_current() {
        for url in urls {
            abrir(app, &url, true);
        }
    }
}

/// La interfaz lo pide al montar: el enlace con el que arrancó la app, si hubo uno.
#[tauri::command]
pub fn enlace_pendiente(app: AppHandle) -> Option<Value> {
    app.state::<Pendiente>().0.lock().unwrap().take()
}
