//! Supervisor del motor Python.
//!
//! Lanza `python -m minuta` (o el ejecutable empaquetado) con un token aleatorio en `MINUTA_TOKEN` y `--padre`,
//! lee el puerto de la primera línea de su salida y lo entrega a la interfaz por `motor_conexion`.
//! Si el motor se cae, lo vuelve a lanzar con espera creciente (1 s, 2 s, 4 s... hasta 60 s).
//!
//! El motor queda dentro de un Job Object con KILL_ON_JOB_CLOSE: si la app muere de cualquier forma, Windows mata
//! el árbol entero (motor y sus `claude -p`) y nunca queda un proceso huérfano grabando. Al salir por la bandeja,
//! antes se le pide al motor que se apague solo, para que cierre la grabación en curso.

use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter};

const SIN_VENTANA: u32 = 0x0800_0000; // CREATE_NO_WINDOW
const ESPERA_ARRANQUE: Duration = Duration::from_secs(60);
const ESPERA_MAXIMA: Duration = Duration::from_secs(60);
const ESTABLE: Duration = Duration::from_secs(60);

#[derive(Clone, Serialize)]
pub struct Conexion {
    pub puerto: u16,
    pub token: String,
}

#[derive(Default)]
struct Interno {
    conexion: Option<Conexion>,
    vivo: bool,
    saliendo: bool,
    ultimo_error: Option<String>,
}

#[derive(Default)]
pub struct Motor {
    interno: Mutex<Interno>,
    cambio: Condvar,
}

impl Motor {
    /// Espera hasta `espera` a que el motor esté listo.
    pub fn conexion(&self, espera: Duration) -> Result<Conexion, String> {
        let limite = Instant::now() + espera;
        let mut i = self.interno.lock().unwrap();
        loop {
            if let Some(c) = &i.conexion {
                return Ok(c.clone());
            }
            let ahora = Instant::now();
            if ahora >= limite {
                return Err(match &i.ultimo_error {
                    Some(e) => format!("El motor de Minuta no arrancó: {e}"),
                    None => "El motor de Minuta todavía está arrancando.".into(),
                });
            }
            i = self.cambio.wait_timeout(i, limite - ahora).unwrap().0;
        }
    }

    pub fn arrancar(self: &Arc<Self>, app: AppHandle) {
        let motor = self.clone();
        thread::Builder::new()
            .name("supervisor".into())
            .spawn(move || motor.supervisar(app))
            .expect("no se pudo crear el hilo del supervisor");
    }

    fn supervisar(&self, app: AppHandle) {
        let trabajo = Trabajo::nuevo();
        let mut espera = Duration::from_secs(1);
        loop {
            if self.interno.lock().unwrap().saliendo {
                break;
            }
            let inicio = Instant::now();
            let token = token_nuevo();
            match lanzar(&token) {
                Ok(hijo) => {
                    if let Some(t) = &trabajo {
                        t.asignar(&hijo);
                    }
                    self.correr(&app, hijo, token);
                }
                Err(e) => self.fallo(&app, format!("no se pudo lanzar: {e}")),
            }
            if self.interno.lock().unwrap().saliendo {
                break;
            }
            if inicio.elapsed() > ESTABLE {
                espera = Duration::from_secs(1);
            }
            registrar(&format!("se relanza el motor en {} s", espera.as_secs()));
            thread::sleep(espera);
            espera = (espera * 2).min(ESPERA_MAXIMA);
        }
        let mut i = self.interno.lock().unwrap();
        i.vivo = false;
        self.cambio.notify_all();
    }

    /// Espera el aviso de arranque, publica la conexión y se queda esperando a que el proceso termine.
    fn correr(&self, app: &AppHandle, mut hijo: Child, token: String) {
        let salida = hijo.stdout.take().expect("stdout del motor");
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            // La primera línea es el aviso; el resto se descarta, pero hay que leerlo para que la tubería no se llene.
            let mut lector = BufReader::new(salida);
            let mut linea = String::new();
            let mut primera = true;
            while matches!(lector.read_line(&mut linea), Ok(n) if n > 0) {
                if primera {
                    let _ = tx.send(linea.clone());
                    primera = false;
                }
                linea.clear();
            }
        });

        let aviso = match rx.recv_timeout(ESPERA_ARRANQUE) {
            Ok(l) => serde_json::from_str::<serde_json::Value>(&l).unwrap_or_default(),
            Err(_) => {
                let _ = hijo.kill();
                let _ = hijo.wait();
                return self.fallo(app, "no respondió al arrancar".into());
            }
        };
        let puerto = aviso.get("puerto").and_then(|p| p.as_u64()).unwrap_or(0) as u16;
        if aviso.get("listo").and_then(|l| l.as_bool()) != Some(true) || puerto == 0 {
            let _ = hijo.wait();
            let error = aviso.get("error").and_then(|e| e.as_str()).unwrap_or("salió sin avisar el puerto");
            return self.fallo(app, error.into());
        }

        {
            let mut i = self.interno.lock().unwrap();
            i.conexion = Some(Conexion { puerto, token });
            i.vivo = true;
            i.ultimo_error = None;
            self.cambio.notify_all();
        }
        registrar(&format!("motor listo en 127.0.0.1:{puerto} (pid {})", hijo.id()));
        let _ = app.emit("motor", serde_json::json!({ "estado": "listo" }));

        let codigo = hijo.wait().map(|s| s.code().unwrap_or(-1)).unwrap_or(-1);
        let saliendo = {
            let mut i = self.interno.lock().unwrap();
            i.conexion = None;
            i.vivo = false;
            self.cambio.notify_all();
            i.saliendo
        };
        if !saliendo {
            self.fallo(app, format!("se cerró con código {codigo}"));
        }
    }

    fn fallo(&self, app: &AppHandle, error: String) {
        registrar(&format!("motor: {error}"));
        self.interno.lock().unwrap().ultimo_error = Some(error.clone());
        let _ = app.emit("motor", serde_json::json!({ "estado": "caido", "error": error }));
    }

    /// Le pide al motor que se apague solo (cierra la grabación en curso) y espera hasta `espera`.
    /// Si no llega a tiempo, al terminar la app el Job Object lo mata igual.
    pub fn apagar(&self, espera: Duration) {
        let estaba_vivo = {
            let mut i = self.interno.lock().unwrap();
            i.saliendo = true;
            i.vivo
        };
        if !estaba_vivo {
            return;
        }
        if let Err(e) = self.pedir("POST", "/api/apagar", None) {
            registrar(&format!("no se pudo pedir el apagado: {e}"));
            return;
        }
        let limite = Instant::now() + espera;
        let mut i = self.interno.lock().unwrap();
        while i.vivo {
            let ahora = Instant::now();
            if ahora >= limite {
                registrar("el motor no terminó a tiempo: lo cierra el Job Object");
                return;
            }
            i = self.cambio.wait_timeout(i, limite - ahora).unwrap().0;
        }
    }

    /// Pedido HTTP mínimo a la API local (la bandeja y la salida no dependen de la interfaz web).
    pub fn pedir(&self, metodo: &str, ruta: &str, cuerpo: Option<&str>) -> Result<serde_json::Value, String> {
        self.pedir_con_espera(metodo, ruta, cuerpo, Duration::from_secs(5))
    }

    /// Como `pedir`, para lo que tarda más de 5 s en responder (transcribir un dictado).
    pub fn pedir_con_espera(
        &self,
        metodo: &str,
        ruta: &str,
        cuerpo: Option<&str>,
        espera: Duration,
    ) -> Result<serde_json::Value, String> {
        let c = self.conexion(Duration::ZERO)?;
        let destino = SocketAddr::from((Ipv4Addr::LOCALHOST, c.puerto));
        let mut s = TcpStream::connect_timeout(&destino, Duration::from_secs(2)).map_err(|e| e.to_string())?;
        s.set_read_timeout(Some(espera)).ok();
        let cuerpo = cuerpo.unwrap_or("");
        write!(
            s,
            "{metodo} {ruta} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {}\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{cuerpo}",
            c.token,
            cuerpo.len()
        )
        .map_err(|e| e.to_string())?;
        let mut respuesta = String::new();
        s.read_to_string(&mut respuesta).map_err(|e| e.to_string())?;
        let (cabecera, cuerpo) = respuesta.split_once("\r\n\r\n").ok_or("respuesta incompleta")?;
        let estado = cabecera.split_whitespace().nth(1).unwrap_or("");
        if !estado.starts_with('2') {
            return Err(format!("HTTP {estado}"));
        }
        serde_json::from_str(cuerpo).map_err(|e| e.to_string())
    }
}

/// Qué Python se lanza: `MINUTA_MOTOR` si está definido; en la versión instalada, el embebible de `motor\` junto a la
/// app (lo arma `scripts/empaquetar_motor.py`); en desarrollo, el del venv del repo. `MINUTA_MOTOR_ARGS` agrega
/// argumentos (p. ej. `--sin-captura`).
fn lanzar(token: &str) -> std::io::Result<Child> {
    let python = match std::env::var("MINUTA_MOTOR") {
        Ok(ruta) => PathBuf::from(ruta),
        Err(_) if cfg!(debug_assertions) => {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(r"..\..\motor\.venv\Scripts\python.exe")
        }
        Err(_) => std::env::current_exe()?.with_file_name("motor").join("python.exe"),
    };
    let mut comando = Command::new(python);
    comando.args(["-m", "minuta"]);
    comando.args(["--padre", &std::process::id().to_string()]);
    if let Ok(extra) = std::env::var("MINUTA_MOTOR_ARGS") {
        comando.args(extra.split_whitespace());
    }
    let errores = OpenOptions::new().create(true).append(true).open(carpeta_logs().join("motor-errores.log"));
    comando
        .env("MINUTA_TOKEN", token)
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(errores.map(Stdio::from).unwrap_or_else(|_| Stdio::null()))
        .creation_flags(SIN_VENTANA)
        .spawn()
}

fn token_nuevo() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("sin fuente de azar del sistema");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn carpeta_logs() -> PathBuf {
    let datos = std::env::var("MINUTA_DATOS")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_default()).join("ar.fmare.minuta"));
    let logs = datos.join("logs");
    let _ = fs::create_dir_all(&logs);
    logs
}

pub fn registrar(mensaje: &str) {
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(carpeta_logs().join("app.log")) {
        let ahora = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        let _ = writeln!(f, "{} {mensaje}", ahora.as_secs());
    }
}

/// Job Object de Windows con KILL_ON_JOB_CLOSE. El handle no se cierra nunca a mano: lo cierra Windows cuando
/// termina la app, y con él mueren todos los procesos asignados.
struct Trabajo(windows_sys::Win32::Foundation::HANDLE);

unsafe impl Send for Trabajo {}

impl Trabajo {
    fn nuevo() -> Option<Self> {
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if h.is_null() {
                registrar("no se pudo crear el Job Object");
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                registrar("no se pudo configurar el Job Object");
                return None;
            }
            Some(Self(h))
        }
    }

    fn asignar(&self, hijo: &Child) {
        let ok = unsafe { windows_sys::Win32::System::JobObjects::AssignProcessToJobObject(self.0, hijo.as_raw_handle()) };
        if ok == 0 {
            registrar("no se pudo asignar el motor al Job Object");
        }
    }
}
