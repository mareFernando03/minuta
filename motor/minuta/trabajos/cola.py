"""Cola de trabajos: lleva cada grabación de «grabada» a «archivada» (o «revisar»).

Tipos de trabajo:
- `transcribir`: clasifica, elige el motor según la regla de privacidad de la carpeta, transcribe, fusiona las
  pistas, corrige con el glosario, guarda la reunión y la exporta en formato Granola.
- `repasar`: solo para lo transcripto en local con `small`; lo vuelve a pasar con `large-v3-turbo` cuando la
  notebook está enchufada y sin uso. La nube ya da más calidad que turbo y no se repasa.
- `diarizar`: separa las voces de «Ellos» (diarizacion/), en esta PC.
- `resumir`: resumen con citas y acciones propuestas, con Claude (inteligencia/resumen.py). Solo para lo que puede
  salir de la PC; si la reunión se va a repasar, espera a la versión repasada.

El texto de una reunión va a Claude (corrección con el glosario, resumen) con la misma regla que el audio va a la
nube: tiene que estar identificada, no marcada «solo en esta PC» y su carpeta tiene que permitir algún motor en la nube.

Un trabajo que no puede correr ahora (a batería, grabando, notebook en uso) vuelve a la cola sin contar como error.
Un error real se reintenta con espera creciente; al quinto queda en «error» con la explicación para la app.
"""
import json
import logging
import shutil
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from minuta.captura import mezcla
from minuta.clasificacion.calendarios import clasificar
from minuta.correccion.glosario import corregir_turnos
from minuta import diarizacion
from minuta.datos import db, exportar
from minuta.inteligencia import resumen
from minuta.transcripcion import local, nube

log = logging.getLogger("minuta.cola")

ESPERA_S = 5
OCIOSA_S = 600
MAX_INTENTOS = 5
PASADA_LOCAL = {"transcribir": "small", "repasar": "large-v3-turbo"}
NOMBRES_MOTOR = {"assemblyai": "AssemblyAI", "groq": "Groq"}
CARPETA_SIN_IDENTIFICAR = db.CARPETA_SIN_IDENTIFICAR


class Esperar(Exception):
    """El trabajo no puede correr ahora; se reintenta más tarde sin contar como fallo."""


class Cola(threading.Thread):
    def __init__(self, evento=lambda tipo, datos: None, grabando=lambda: False):
        super().__init__(name="cola", daemon=True)
        self.evento, self.grabando = evento, grabando
        self._parar = threading.Event()
        self._despertar = threading.Event()

    def parar(self):
        self._parar.set()
        self._despertar.set()

    def despertar(self):
        self._despertar.set()

    def run(self):
        db.reanudar_trabajos_cortados()
        ultimo_mantenimiento = None
        while not self._parar.is_set():
            if ultimo_mantenimiento != date.today():
                borrar_audio_vencido()
                ultimo_mantenimiento = date.today()
            t = db.tomar_trabajo(["transcribir", "repasar", "diarizar", "resumir"])
            if t is None:
                self._despertar.wait(ESPERA_S)
                self._despertar.clear()
                continue
            self.ejecutar(t)

    def ejecutar(self, t):
        gid = t["grabacion_id"]
        try:
            if t["tipo"] == "transcribir":
                self.transcribir(t)
            elif t["tipo"] == "diarizar":
                self.diarizar(t)
            elif t["tipo"] == "resumir":
                self.resumir(t)
            else:
                self.repasar(t)
            db.terminar_trabajo(t["id"], "hecho")
        except Esperar as e:
            db.terminar_trabajo(t["id"], "pendiente", progreso=f"esperando: {e}",
                                proximo_intento=(datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds"))
            db.conexion().execute("UPDATE trabajos SET intentos = intentos - 1 WHERE id = ?", (t["id"],))
        except Exception as e:
            log.exception("falló %s de %s", t["tipo"], gid)
            if t["tipo"] == "resumir":  # la app muestra «Resumiendo…» hasta que llegue algo, también un error
                self.evento("resumen", {"id": (db.grabacion(gid) or {}).get("reunion_id"), "error": explicar(e)})
            if t["intentos"] < MAX_INTENTOS:
                espera = timedelta(minutes=2 ** t["intentos"])
                db.terminar_trabajo(t["id"], "pendiente", error=explicar(e),
                                    proximo_intento=(datetime.now() + espera).isoformat(timespec="seconds"))
            else:
                db.terminar_trabajo(t["id"], "error", error=explicar(e))
                if t["tipo"] == "transcribir":  # repasar y diarizar son mejoras: la reunión ya está guardada
                    db.actualizar_grabacion(gid, estado="error", error=explicar(e))
                    self.evento("error", {"id": gid, "error": explicar(e)})
                elif t["tipo"] == "repasar":  # el resumen esperaba al repaso: se hace con la versión de small
                    g = db.grabacion(gid)
                    if g and g.get("reunion_id"):
                        self._encolar_resumen(g)

    # --- transcribir ---------------------------------------------------------------------------------------------

    def transcribir(self, t):
        gid = t["grabacion_id"]
        g = db.grabacion(gid)
        carpeta_audio = Path(g["carpeta"])
        db.actualizar_grabacion(gid, estado="procesando")
        inicio = datetime.fromisoformat(g["inicio"])
        fin = datetime.fromisoformat(g["fin"]) if g.get("fin") else inicio
        previa = db.reunion_basica(g["reunion_id"]) if g.get("reunion_id") else None
        if previa:
            # Reproceso: la carpeta, el título y el estado que quedaron (quizá corregidos a mano) mandan sobre una
            # clasificación nueva. Es el mismo criterio con que la API y la app ofrecen los motores.
            identificada, carpeta = g["estado"] == "archivada", previa.get("carpeta")
            c = {"titulo": previa["titulo"], "participantes": previa.get("participantes") or [],
                 "origen": previa.get("origen"), "folders": [carpeta] if carpeta else []}
        else:
            c = clasificar_grabacion(g, inicio, fin)
            identificada = c is not None
            carpeta = (c or {}).get("folders", [None])[0] if c and c.get("folders") else None
        permitidos = motores_permitidos(g, carpeta, identificada)
        forzado = t["parametros"].get("motor")  # reprocesar con un motor elegido a mano, dentro de lo permitido
        if forzado and not motor_permitido(forzado, permitidos):
            log.warning("%s: %s no está permitido para esta grabación: se usa %s", gid, forzado, permitidos[0])
        orden = [forzado] if forzado and motor_permitido(forzado, permitidos) else permitidos

        mic = sistema = None
        usado = None
        for motor in orden:
            if motor == "local":
                mic, sistema = self._local(t, carpeta_audio, "transcribir", corte_s=g.get("corte_s"))
                usado = PASADA_LOCAL["transcribir"]
                break
            try:
                db.progreso_trabajo(t["id"], f"transcribiendo tu micrófono en {NOMBRES_MOTOR.get(motor, motor)}", 0.05)
                mic, voz_m = nube.MOTORES[motor](local.leer_pista(carpeta_audio, "mic", g.get("corte_s")))
                db.progreso_trabajo(t["id"], f"transcribiendo a los demás en {NOMBRES_MOTOR.get(motor, motor)}", 0.4)
                sistema, voz_s = nube.MOTORES[motor](local.leer_pista(carpeta_audio, "sistema", g.get("corte_s")))
                voz = voz_m + voz_s
                db.registrar_uso(motor, voz, voz / 3600 * nube.PRECIO_HORA[motor], gid)
                usado = motor
                break
            except nube.SinNube as e:
                log.warning("%s: %s no disponible: %s", gid, motor, e)
        if usado is None:
            raise RuntimeError("ningún motor disponible según la regla de privacidad de la carpeta")

        turnos = local.fusionar_turnos(mic, sistema, g.get("corte_s"), voz_mic=voz_del_mic(g, carpeta_audio))
        rid = g.get("reunion_id") or f"local-{uuid.uuid4()}"
        titulo = c["titulo"] if c else g.get("titulo_manual") or f"Grabación de las {inicio:%H:%M}"
        se_repasa = usado == PASADA_LOCAL["transcribir"]
        self._guardar(t, g, rid, turnos, usado, titulo, carpeta, c or {}, identificada, resumir=not se_repasa)
        if se_repasa:
            db.encolar("repasar", gid)

    def repasar(self, t):
        gid = t["grabacion_id"]
        g = db.grabacion(gid)
        # Si se corrigió o se borró algo a mano, el repaso no la pisa: esas correcciones valen más que la de turbo.
        if not g.get("reunion_id"):
            return
        if (db.reunion_basica(g["reunion_id"]) or {}).get("editada_en"):
            return self._encolar_resumen(g)
        mic, sistema = self._local(t, Path(g["carpeta"]), "repasar", corte_s=g.get("corte_s"))
        r = db.reunion(g["reunion_id"])
        if r.get("editada_en"):  # se editó mientras corría (o se borró un tramo que turbo ya había leído)
            log.info("%s: la reunión se corrigió a mano durante el repaso: no se reemplaza", gid)
            return self._encolar_resumen(g)
        turnos = local.fusionar_turnos(mic, sistema, g.get("corte_s"), voz_mic=voz_del_mic(g, Path(g["carpeta"])))
        c = {"participantes": r["participantes"], "origen": r["origen"]}
        self._guardar(t, g, r["id"], turnos, PASADA_LOCAL["repasar"], r["titulo"], r["carpeta"], c,
                      identificada=g["estado"] != "revisar")

    def diarizar(self, t):
        """Separa las voces de «Ellos», parte los turnos donde cambia la voz y sugiere (o pone) nombres. Corre en
        esta PC y solo enchufada: una hora de reunión son unos 8 minutos de procesador."""
        gid = t["grabacion_id"]
        g = db.grabacion(gid)
        rid = g.get("reunion_id")
        if not rid or not any(Path(g["carpeta"]).glob("sistema-*.wav")):
            return  # sin reunión o el audio ya se borró
        if not local.enchufada():
            raise Esperar("la notebook está a batería")
        if self.grabando():
            raise Esperar("hay una reunión grabándose")
        db.progreso_trabajo(t["id"], "separando las voces de los demás", 0.1)
        try:
            tramos, huellas = diarizacion.en_proceso_aparte(Path(g["carpeta"]), g.get("corte_s"),
                                                            float(db.ajuste("diarizacion_umbral")))
        except diarizacion.SinModelos as e:
            raise Esperar(str(e)) from e
        r = db.reunion(rid)
        if not r:
            return
        db.aplicar_voces(rid, diarizacion.repartir(r["segmentos"], tramos))
        # Toda voz que quedó en algún turno lleva fila, aunque sea muy corta para tener huella.
        hablado = {}
        for i, f, v in tramos:
            hablado[v] = hablado.get(v, 0.0) + f - i
        db.guardar_voces_reunion(rid, {f"V{v + 1}": huellas.get(v, (None, round(s, 1))) for v, s in hablado.items()})
        db.emparejar(rid, auto=bool(db.ajuste("diarizacion_auto")))
        self.evento("voces", {"id": rid, "voces": len(hablado)})

    def _local(self, t, carpeta_audio, tipo, corte_s=None):
        """Whisper local, bloque por bloque, retomando lo ya hecho. Solo enchufada; turbo además pide la notebook
        ociosa, porque ocupa el procesador más de media hora por hora de reunión."""
        nombre, pasada = PASADA_LOCAL[tipo], 1 if tipo == "transcribir" else 2
        # Solo los bloques que empiezan antes del corte: lo posterior no es la reunión.
        bloques = [(p, w) for p in ("mic", "sistema") for w in local.bloques_hasta(carpeta_audio, p, corte_s)]
        if not bloques:
            raise RuntimeError("la grabación no tiene audio: se borró o nunca se escribió")
        for i, (pista, wav) in enumerate(bloques, start=1):
            n = int(wav.stem.split("-")[1])
            salida = carpeta_audio / f"seg-{pasada}-{pista}-{n:03d}.json"
            if salida.exists():
                continue
            if not local.enchufada():
                raise Esperar("la notebook está a batería")
            if self.grabando():
                raise Esperar("hay una reunión grabándose")
            if tipo == "repasar" and local.segundos_sin_uso() < OCIOSA_S:
                raise Esperar("la notebook está en uso")
            db.progreso_trabajo(t["id"], f"transcribiendo en esta PC, parte {i} de {len(bloques)}",
                                (i - 1) / len(bloques) * 0.85)
            segs = local.transcribir_bloque(nombre, wav, n * 300)
            salida.write_text(_json(segs), encoding="utf-8")
        db.registrar_uso("local", sum(w.stat().st_size for _, w in bloques) / 2 / local.SR, 0.0, t["grabacion_id"])
        cargar = lambda p: [s for f in sorted(carpeta_audio.glob(f"seg-{pasada}-{p}-*.json")) for s in _cargar(f)]
        return cargar("mic"), cargar("sistema")

    def resumir(self, t):
        gid = t["grabacion_id"]
        g = db.grabacion(gid)
        r = db.reunion(g["reunion_id"]) if g.get("reunion_id") else None
        # Se vuelve a mirar la regla: la reunión pudo cambiar de carpeta desde que se encoló.
        if not r or not puede_salir(g, r.get("carpeta"), g["estado"] == "archivada"):
            return
        if not any(s["texto"].strip() for s in r["segmentos"]):
            return self.evento("resumen", {"id": r["id"], "error": "La reunión no tiene texto para resumir"})
        nombres = db.nombres_de_turnos(r, {"mic": "Vos", "sistema": "Ellos", **(r.get("hablantes") or {})})
        plantilla = (db.ajuste("plantillas") or {}).get(r.get("carpeta") or "", "")
        pedido = t["parametros"].get("pedido") or ""
        duracion = max([s.get("fin") or s.get("inicio") or 0 for s in r["segmentos"]] + [m["t"] for m in r["marcas"]]) or None
        db.progreso_trabajo(t["id"], "resumiendo con Claude", 0.2)
        try:
            hecho, costo = resumen.pedir(resumen.entrada(r, nombres, plantilla, pedido), duracion)
        except Exception as e:
            raise resumen.SinResumen(str(e)) from e
        db.registrar_uso("sonnet", 0, costo, gid)
        guardado = db.guardar_resumen(r["id"], {"secciones": hecho["secciones"], "generado": db.ahora(),
                                                "pedido": pedido or None, "con_plantilla": bool(plantilla.strip())},
                                      hecho["acciones"], editada_en=r.get("editada_en"))
        if not guardado:  # se editó o se borró mientras tanto: se vuelve a pedir sobre lo actual, si sigue existiendo
            g = db.grabacion(gid)
            if g and g.get("reunion_id") and db.reunion_basica(g["reunion_id"]):
                self._encolar_resumen(g, aunque_corra=True, pedido=pedido)
            return
        self.evento("resumen", {"id": r["id"], "acciones": len(hecho["acciones"])})

    def _encolar_resumen(self, g, aunque_corra=False, **parametros):
        if db.ajuste("resumir") and puede_salir(g, (db.reunion_basica(g["reunion_id"]) or {}).get("carpeta"),
                                                g["estado"] == "archivada"):
            db.encolar("resumir", g["id"], aunque_corra=aunque_corra, **parametros)

    def _guardar(self, t, g, rid, turnos, motor, titulo, carpeta, c, identificada, resumir=True):
        gid = g["id"]
        correcciones = []
        if puede_salir(g, carpeta, identificada):
            db.progreso_trabajo(t["id"], "corrigiendo nombres con el glosario", 0.88)
            try:
                turnos, aplicados, costo = corregir_turnos(turnos, local.como_texto)
                correcciones = [{"original": o, "correcto": n} for o, n in aplicados]
                db.registrar_uso("haiku", 0, costo, gid)
            except Exception as e:
                log.warning("%s: corrección con el glosario falló, se guarda sin corregir: %s", gid, e)
        db.guardar_reunion({"id": rid, "fuente": "minuta", "grabacion_id": gid, "titulo": titulo, "carpeta": carpeta,
                            "inicio": g["inicio"], "fin": g.get("fin"), "origen": c.get("origen"),
                            "participantes": c.get("participantes") or [], "motor": motor,
                            "correcciones": correcciones}, turnos)
        db.actualizar_grabacion(gid, reunion_id=rid, estado="archivada" if identificada else "revisar", error=None)
        db.asignar_marcas(gid, rid)
        if db.ajuste("diarizar") and any(s["canal"] == "sistema" for s in turnos):
            db.encolar("diarizar", gid)
        if resumir:
            self._encolar_resumen(dict(g, reunion_id=rid, estado="archivada" if identificada else "revisar"))
        try:  # el reproductor de la app usa la mezcla en Opus: generarla ahora hace que el primer play sea instantáneo
            mezcla.generar(g["carpeta"])
        except Exception as e:
            log.warning("%s: no se pudo generar la mezcla de audio: %s", gid, e)
        if identificada:
            exportar_reunion(rid)
        self.evento("reunion_lista", {"id": rid, "grabacion": gid, "titulo": titulo, "motor": motor,
                                      "revisar": not identificada})


# --- funciones sueltas (también las usa la API) ------------------------------------------------------------------

def clasificar_grabacion(g, inicio, fin):
    """`clasificar` más lo que el usuario dijo al grabar a mano: su título y su carpeta mandan sobre el calendario (que
    igual aporta los participantes si la presencial estaba agendada). Sin carpeta, una grabación manual sin evento
    queda sin identificar: el título solo no alcanza para que salga de la PC. En una manual las pestañas abiertas no
    son la reunión: solo el calendario o la carpeta elegida la identifican."""
    manual = "manual" in (g.get("apps") or [])
    c = clasificar(inicio, fin, {} if manual else g.get("titulos_ventana") or {}, g.get("apps") or [])
    titulo, carpeta = g.get("titulo_manual"), g.get("carpeta_manual")
    if not (titulo or carpeta):
        return c
    if c is None:
        if not carpeta:
            return None
        return {"titulo": titulo or f"Reunión presencial de las {inicio:%H:%M}", "participantes": [],
                "folders": [carpeta], "origen": "manual"}
    return dict(c, titulo=titulo or c["titulo"], folders=[carpeta] if carpeta else c.get("folders", []))


MIC_MEDIDO = "Micrófono (Realtek"  # el umbral de local.VOZ_MIC_MIN se midió con este micrófono


def voz_del_mic(g, carpeta_audio):
    """La envolvente del mic para filtrar lo ajeno. None (no se filtra) en una grabación a mano, donde las voces del
    cuarto entran bajas y son la reunión, y con un micrófono distinto del medido o que cambió en medio: con otra
    ganancia el umbral podría borrar al usuario sin avisar."""
    if "manual" in (g.get("apps") or []):
        return None
    try:
        info = json.loads((Path(carpeta_audio) / "audio.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not str(info.get("mic") or "").startswith(MIC_MEDIDO) or info.get("cambios_mic"):
        return None
    return local.envolvente(carpeta_audio, "mic", g.get("corte_s"))


def privacidad_para(carpeta):
    return db.motores_para(db.ajuste("privacidad"), carpeta)


def motores_permitidos(g, carpeta, identificada):
    """Los motores que puede usar una grabación, en orden: los de su carpeta, solo local si no está identificada o si
    se marcó «solo en esta PC». Un reproceso a mano elige dentro de esta lista, nunca fuera."""
    if g.get("solo_local"):
        return ["local"]
    return privacidad_para(carpeta if identificada else CARPETA_SIN_IDENTIFICAR)


def motor_permitido(motor, permitidos):
    return motor == "local" or motor in permitidos  # lo local no sale de la PC: siempre se puede


def motores_de_grabacion(g):
    """Lo permitido según el estado actual: identificada es lo archivado (lo que está «para revisar» no salió)."""
    r = db.reunion_basica(g["reunion_id"]) if g.get("reunion_id") else None
    return motores_permitidos(g, (r or {}).get("carpeta"), g["estado"] == "archivada")


def puede_salir(g, carpeta, identificada):
    """Si el texto de la reunión puede ir a Claude: la misma regla que el audio a la nube (db.texto_puede_salir)."""
    return db.texto_puede_salir(db.ajuste("privacidad"), carpeta, identificada, g.get("solo_local"))


def exportar_reunion(rid):
    r = db.reunion(rid)
    turnos = [{"canal": s["canal"], "texto": s["texto"]} for s in r["segmentos"]]
    exportar.archivar(db.ajuste("exportar_a"), datetime.fromisoformat(r["inicio"]), r["titulo"],
                      [r["carpeta"]] if r["carpeta"] else [], r["participantes"], local.como_texto(turnos),
                      id_previo=rid)
    db.editar_reunion(rid, exportado_en=db.ahora())


def borrar_audio_vencido():
    """Borra los WAV de las grabaciones cuya retención venció. Lo que quedó para revisar o con error se conserva."""
    hoy = date.today().isoformat()
    for g in db.grabaciones(estados=["archivada"], limite=10000):
        if g.get("audio_hasta") and g["audio_hasta"] < hoy:
            pendiente = db.conexion().execute(
                "SELECT 1 FROM trabajos WHERE grabacion_id = ? AND estado IN ('pendiente', 'corriendo')", (g["id"],)).fetchone()
            if not pendiente:
                for archivo in [*Path(g["carpeta"]).glob("*.wav"), Path(g["carpeta"]) / mezcla.NOMBRE]:
                    archivo.unlink(missing_ok=True)


def borrar_grabacion(gid):
    g = db.grabacion(gid)
    if g:
        shutil.rmtree(g["carpeta"], ignore_errors=True)
        if g.get("reunion_id"):
            db.borrar_reunion(g["reunion_id"])
        db.actualizar_grabacion(gid, estado="descartada", error="borrada por el usuario")


def explicar(e):
    """El error en palabras para la app, sin la traza técnica."""
    texto = str(e)
    if isinstance(e, resumen.SinResumen):
        return f"No se pudo resumir con Claude: {texto[:200]}"
    if isinstance(e, nube.SinNube) or "red:" in texto:
        return f"No se pudo usar el servicio de transcripción: {texto[:200]}"
    if "claude" in texto.lower():
        return "No se pudo correr la corrección con el glosario (Claude). La reunión se guardó igual."
    return texto[:300] or type(e).__name__


def _json(x):
    import json
    return json.dumps(x, ensure_ascii=False)


def _cargar(f):
    import json
    return json.loads(f.read_text(encoding="utf-8"))
