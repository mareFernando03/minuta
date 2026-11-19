"""API local del motor, para la app de escritorio.

Seguridad (la API ve el audio y las transcripciones de reuniones con clientes):
- Escucha solo en 127.0.0.1 (lo fija `__main__`).
- Cada pedido trae el token del arranque: `Authorization: Bearer <token>`, o `?t=<token>` donde el navegador no
  deja poner encabezados (el `<audio>` y el WebSocket). Comparación en tiempo constante.
- Si el pedido trae `Origin`, tiene que ser el de la app; así otra página abierta en el navegador no puede usarla.
- Los secretos se pueden escribir pero nunca leer: la API solo dice si están configurados.
"""
import asyncio
import logging
import hmac
import re
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from datetime import date, datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from minuta import __version__, config, secretos
from minuta.captura import mezcla, silenciar
from minuta.correccion.glosario import agregar_deformacion as agregar_al_glosario, cambio_de_palabra
from minuta.datos import db
from minuta.inteligencia import brief
from minuta.trabajos import cola as cola_mod
from minuta.transcripcion import local, nube

ORIGENES = {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost", "http://localhost:1420"}
MOTORES_VALIDOS = {"assemblyai", "groq", "local"}
CARPETAS = ["Seguimiento Northwind", "Contoso Fabrikam", "Orbit", "Globex", "Acme", "Facultad"]
CREDITO_ASSEMBLYAI = 50.0
CUOTA_GROQ_S = 8 * 3600


NOTA_MAX = 2000
TEXTO_MAX = 200_000  # hay turnos importados de Granola de 149 mil caracteres


class CambiosMarca(BaseModel):
    """Lo editable de una marca. Con tipos: un `t` como texto rompía el Markdown y la transcripción del MCP."""
    model_config = ConfigDict(extra="forbid")
    etiqueta: Literal["accion", "decision", "pregunta"] | None = None
    nota: Annotated[str, StringConstraints(max_length=NOTA_MAX)] | None = None
    t: Annotated[float, Field(ge=0)] | None = None


class CambiosAccion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estado: Literal["propuesta", "aceptada", "descartada"] | None = None
    texto: Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)] | None = None
    responsable: Annotated[str, StringConstraints(max_length=80, strip_whitespace=True)] | None = None


def _mmss(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _de_la_grabacion(g):
    """Lo que la app necesita para nombrar una grabación que todavía no es reunión."""
    vistos = sorted((g.get("titulos_ventana") or {}).items(), key=lambda x: -x[1])
    titulo = db.titulo_reunion(g["reunion_id"]) if g.get("reunion_id") else None
    return {"inicio": g.get("inicio"), "titulo": titulo, "ventanas": [t for t, _ in vistos[:5]], "apps": g.get("apps") or []}


def _en_proceso(t):
    """Un trabajo de la cola con su avance y cuánto falta, estimado por lo que tardó hasta ahora."""
    g = db.grabacion(t["grabacion_id"]) or {}
    # La velocidad sale de lo avanzado en esta corrida: lo retomado de antes (bloques ya hechos) no cuenta.
    restante = None
    hecho = (t.get("avance") or 0) - (t.get("avance_inicial") or 0)
    if t["estado"] == "corriendo" and hecho >= 0.05 and t.get("empezado"):
        transcurrido = (datetime.now() - datetime.fromisoformat(t["empezado"])).total_seconds()
        restante = round(transcurrido * (1 - t["avance"]) / hecho)
    return {**t, **_de_la_grabacion(g), "restante_s": restante}


def crear_app(token, vigia=None, cola=None, bus=None, apagar=None, dictado=None):
    @asynccontextmanager
    async def ciclo_de_vida(_app):
        if bus:
            bus.conectar_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="Minuta", version=__version__, docs_url=None, redoc_url=None, openapi_url="/api/openapi.json",
                  lifespan=ciclo_de_vida)
    # La interfaz (tauri://localhost, o localhost:1420 en desarrollo) es otro origen que 127.0.0.1: sin CORS el
    # WebView no deja leer las respuestas. Solo se habilitan los orígenes de la app; el token se sigue exigiendo.
    app.add_middleware(CORSMiddleware, allow_origins=sorted(ORIGENES), allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type"], expose_headers=["Content-Range", "Accept-Ranges"])

    def autorizar(request: Request):
        origen = request.headers.get("origin")
        if origen and origen not in ORIGENES:
            raise HTTPException(403, "origen no permitido")
        enviado = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("t", "")
        if not hmac.compare_digest(enviado.encode(), token.encode()):
            raise HTTPException(401, "token inválido")

    seguro = [Depends(autorizar)]

    # --- estado -----------------------------------------------------------------------------------------------

    @app.get("/api/salud")
    def salud():
        return {"ok": True, "version": __version__}

    @app.get("/api/estado", dependencies=seguro)
    def estado():
        vivo = vigia.estado() if vigia else {"grabando": False, "pausado": bool(db.ajuste("pausado"))}
        trabajos = db.trabajos(limite=20)
        con_error = db.grabaciones(estados=["error"])
        return {"ahora": vivo,
                "cola": [_en_proceso(t) for t in trabajos if t["estado"] in ("pendiente", "corriendo")],
                "para_revisar": len(db.grabaciones(estados=["revisar"])),
                "con_error": len(con_error),
                "errores": [{**_de_la_grabacion(g), "grabacion_id": g["id"], "error": g.get("error")} for g in con_error[:10]]}

    @app.post("/api/pausa", dependencies=seguro)
    def pausa(pausado: bool = Body(..., embed=True)):
        db.guardar_ajuste("pausado", pausado)
        if pausado and vigia:
            vigia.pausa_pedida() if hasattr(vigia, "pausa_pedida") else vigia.paso()
        if bus:
            bus.publicar("pausa", {"pausado": pausado})
        return {"pausado": pausado}

    @app.post("/api/apagar", dependencies=seguro)
    def apagar_motor():
        """Lo usa el supervisor al salir: el motor cierra la grabación en curso y termina solo."""
        if apagar is None:
            raise HTTPException(409, "este motor no se puede apagar por la API")
        apagar()
        return {"apagando": True}

    @app.post("/api/grabacion-actual/empezar", dependencies=seguro)
    def empezar_manual(titulo: str | None = Body(None, embed=True, max_length=200),
                       carpeta: str | None = Body(None, embed=True)):
        """Grabar a mano (una reunión presencial). Graba aunque Minuta esté en pausa, sin levantarla."""
        if carpeta is not None and carpeta not in CARPETAS:
            raise HTTPException(422, "carpeta desconocida")
        if not vigia:
            raise HTTPException(503, "el motor corre sin captura")
        gid = vigia.empezar_manual((titulo or "").strip() or None, carpeta)
        if gid is None:
            raise HTTPException(409, "ya se está grabando")
        return {"id": gid}

    def _dictado():
        if not dictado:
            raise HTTPException(503, "el motor corre sin captura")
        return dictado

    @app.post("/api/dictado/empezar", dependencies=seguro)
    def empezar_dictado():
        """Ctrl+Win abajo (app/src-tauri/src/dictado.rs): empieza a escuchar el micrófono."""
        from minuta.dictado import Apagado, Ocupado
        try:
            _dictado().empezar()
        except Apagado:
            raise HTTPException(409, "el dictado está apagado") from None
        except Ocupado:
            raise HTTPException(409, "ya se está dictando") from None
        return {"escuchando": True}

    @app.post("/api/dictado/terminar", dependencies=seguro)
    def terminar_dictado():
        """Ctrl+Win arriba: transcribe y devuelve el texto para pegar. Texto vacío si fue un toque o no hubo voz."""
        try:
            return _dictado().terminar()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"no se pudo transcribir: {e}") from None

    @app.post("/api/dictado/cancelar", dependencies=seguro)
    def cancelar_dictado():
        """Ctrl+Win con otra tecla (Ctrl+Win+D): era un atajo de Windows, no un dictado."""
        _dictado().cancelar()
        return {"escuchando": False}

    @app.get("/api/dictados", dependencies=seguro)
    def ultimos_dictados(limite: int = Query(10, ge=1, le=db.DICTADOS_GUARDADOS)):
        return db.dictados(limite)

    @app.post("/api/grabacion-actual/solo-local", dependencies=seguro)
    def solo_local_actual(solo_local: bool = Body(True, embed=True)):
        gid = vigia.solo_local_actual(solo_local) if vigia else None
        if gid is None:
            raise HTTPException(409, "no hay ninguna grabación en curso")
        return {"id": gid, "solo_local": solo_local}

    @app.post("/api/grabacion-actual/descartar", dependencies=seguro)
    def descartar_actual():
        gid = vigia.descartar_actual() if vigia else None
        if gid is None:
            raise HTTPException(409, "no hay ninguna grabación en curso")
        return {"descartada": gid}

    @app.post("/api/grabacion-actual/cortar", dependencies=seguro)
    def cortar_actual():
        gid = vigia.cortar_actual() if vigia else None
        if gid is None:
            raise HTTPException(409, "no hay ninguna grabación en curso")
        return {"cortada": gid}

    @app.post("/api/grabacion-actual/marca", dependencies=seguro)
    def marcar(etiqueta: str | None = Body(None, embed=True),
               nota: str | None = Body(None, embed=True, max_length=NOTA_MAX)):
        if etiqueta is not None and etiqueta not in db.ETIQUETAS:
            raise HTTPException(422, f"etiqueta desconocida: {etiqueta}")
        m = vigia.marcar(etiqueta, nota) if vigia else None
        if m is None:
            raise HTTPException(409, "no hay ninguna grabación en curso")
        return m

    @app.patch("/api/marcas/{mid}", dependencies=seguro)
    def editar_marca(mid: int, cambios: CambiosMarca):
        if not db.marca(mid):
            raise HTTPException(404, "no existe esa marca")
        return db.editar_marca(mid, **cambios.model_dump(exclude_unset=True))

    @app.delete("/api/marcas/{mid}", dependencies=seguro)
    def borrar_marca(mid: int):
        m = db.marca(mid)
        if not m:
            raise HTTPException(404, "no existe esa marca")
        db.borrar_marca(mid)
        return {"borrada": mid}

    @app.post("/api/grabacion-actual/seguir", dependencies=seguro)
    def seguir_actual():
        gid = vigia.seguir_actual() if vigia else None
        if gid is None:
            raise HTTPException(409, "no hay ninguna grabación en curso")
        return {"sigue": gid}

    # --- reuniones --------------------------------------------------------------------------------------------

    @app.get("/api/reuniones", dependencies=seguro)
    def reuniones(carpeta: str | None = None, antes_de: str | None = None, limite: int = 100):
        return db.reuniones(carpeta=carpeta, antes_de=antes_de, limite=min(limite, 500))

    @app.get("/api/reuniones/{rid}", dependencies=seguro)
    def reunion(rid: str):
        r = db.reunion(rid)
        if not r:
            raise HTTPException(404, "no existe esa reunión")
        g = db.grabacion(r["grabacion_id"]) if r.get("grabacion_id") else None
        r["audio"] = bool(g and any(Path(g["carpeta"]).glob("*.wav")))
        r["audio_hasta"] = g.get("audio_hasta") if g else None
        r["motores"] = cola_mod.motores_de_grabacion(g) if g else []
        # Si Claude la puede leer (MCP): las importadas de Granola ya estaban en la nube.
        r["sale"] = cola_mod.puede_salir(g, r.get("carpeta"), g["estado"] == "archivada") if g else True
        return r

    @app.patch("/api/reuniones/{rid}", dependencies=seguro)
    def editar(rid: str, cambios: dict = Body(...)):
        if not db.reunion(rid):
            raise HTTPException(404, "no existe esa reunión")
        if "carpeta" in cambios and cambios["carpeta"] not in (*CARPETAS, None):
            raise HTTPException(422, "carpeta desconocida")
        db.editar_reunion(rid, **{k: v for k, v in cambios.items() if k in ("titulo", "carpeta", "hablantes")})
        if db.reunion(rid)["exportado_en"]:
            cola_mod.exportar_reunion(rid)
        return db.reunion(rid)

    def _reexportar(rid):
        if (db.reunion_basica(rid) or {}).get("exportado_en"):
            cola_mod.exportar_reunion(rid)

    @app.patch("/api/reuniones/{rid}/segmentos/{sid}", dependencies=seguro)
    def editar_segmento(rid: str, sid: int, texto: str = Body(..., embed=True, min_length=1, max_length=TEXTO_MAX)):
        """Corrige un turno a mano. Si cambió una sola palabra, sugiere cambiar las otras apariciones de la reunión y
        sumarla al glosario («Contosó» -> «Contoso»)."""
        s = db.segmento(sid)
        if not s or s["reunion_id"] != rid:
            raise HTTPException(404, "no existe ese turno en esa reunión")
        if not texto.strip():
            raise HTTPException(422, "el turno no puede quedar vacío: para sacarlo, usá «Borrar este tramo»")
        cambio = cambio_de_palabra(s["texto"], texto)
        nuevo = db.editar_segmento(sid, texto.strip())
        _reexportar(rid)
        sugerencia = None
        if cambio:
            otras = db.contar_en_reunion(rid, cambio[0])
            sugerencia = {"original": cambio[0], "correcto": cambio[1], "otras": otras}
        return {"segmento": nuevo, "sugerencia": sugerencia}

    # --- voces (quién habla dentro de «Ellos») -----------------------------------------------------------------

    def _voz_existente(rid, etiqueta):
        if not any(v["etiqueta"] == etiqueta for v in db.voces_de_reunion(rid)):
            raise HTTPException(404, "esa voz no está en esta reunión")

    @app.post("/api/reuniones/{rid}/voces/{etiqueta}/nombre", dependencies=seguro)
    def nombrar_voz(rid: str, etiqueta: str, nombre: str = Body(..., embed=True, min_length=1, max_length=80)):
        """«Esta voz es Pedro»: queda su huella y se sugiere (o se asigna, con diarizacion_auto) en las demás reuniones."""
        _voz_existente(rid, etiqueta)
        if not nombre.strip() or "\n" in nombre:
            raise HTTPException(422, "el nombre no puede quedar vacío")
        db.nombrar_voz(rid, etiqueta, nombre, auto=bool(db.ajuste("diarizacion_auto")))
        return {"voces": db.voces_de_reunion(rid)}

    @app.delete("/api/reuniones/{rid}/voces/{etiqueta}/nombre", dependencies=seguro)
    def quitar_nombre_voz(rid: str, etiqueta: str):
        _voz_existente(rid, etiqueta)
        db.quitar_nombre_voz(rid, etiqueta)
        return {"voces": db.voces_de_reunion(rid)}

    @app.post("/api/reuniones/{rid}/voces/{etiqueta}/rechazar", dependencies=seguro)
    def rechazar_voz(rid: str, etiqueta: str):
        """«No es Pedro»: no se vuelve a sugerir esa persona para esta voz."""
        _voz_existente(rid, etiqueta)
        db.rechazar_sugerencia(rid, etiqueta)
        return {"voces": db.voces_de_reunion(rid)}

    @app.post("/api/reuniones/{rid}/cambiar-voz", dependencies=seguro)
    def cambiar_voz(rid: str, segmentos: list[int] = Body(..., embed=True, min_length=1, max_length=2000),
                    etiqueta: str = Body(..., embed=True, pattern=r"^V[1-9][0-9]{0,2}$")):
        if not db.reunion_basica(rid):
            raise HTTPException(404, "no existe esa reunión")
        n = db.cambiar_voz(rid, segmentos, etiqueta)
        if n == 0:  # los ids que tiene la app ya no existen: la reunión cambió (se diarizó o se repasó) mientras tanto
            raise HTTPException(409, "esos turnos cambiaron mientras tanto: recargá la reunión y volvé a elegirlos")
        return {"cambiados": n, "voces": db.voces_de_reunion(rid)}

    @app.get("/api/voces", dependencies=seguro)
    def personas():
        """Las personas con nombre: para sugerir al escribir y para olvidarlas desde Ajustes."""
        return db.personas()

    @app.delete("/api/voces/{pid}", dependencies=seguro)
    def olvidar_persona(pid: int):
        """Borra la huella de voz de una persona y su nombre en todas las reuniones."""
        if not db.olvidar_persona(pid):
            raise HTTPException(404, "no existe esa persona")
        return {"ok": True}

    # --- inteligencia: resumen, acciones, ediciones propuestas y brief ---------------------------------------

    @app.post("/api/reuniones/{rid}/resumir", dependencies=seguro)
    def resumir(rid: str, pedido: str | None = Body(None, embed=True, max_length=500)):
        """Resume (o regenera, con un pedido libre: «más corto», «detallá los próximos pasos») con Claude."""
        r = db.reunion_basica(rid)
        if not r:
            raise HTTPException(404, "no existe esa reunión")
        g = db.grabacion(r["grabacion_id"]) if r.get("grabacion_id") else None
        if not g:
            raise HTTPException(409, "solo se resumen las reuniones grabadas con Minuta: las importadas traen su resumen")
        if not cola_mod.puede_salir(g, r.get("carpeta"), g["estado"] == "archivada"):
            raise HTTPException(409, "esta reunión no sale de la PC (sin identificar, «solo en esta PC» o carpeta local)")
        # Si ya había uno esperando, el pedido nuevo lo reemplaza; si hay uno corriendo, este va después.
        tid = db.encolar("resumir", g["id"], aunque_corra=True, pedido=(pedido or "").strip())
        if cola:
            cola.despertar()
        return {"trabajo": tid}

    @app.get("/api/acciones", dependencies=seguro)
    def acciones(estado: Literal["propuesta", "aceptada", "descartada"] | None = None):
        return db.acciones(estado=estado)

    @app.patch("/api/acciones/{aid}", dependencies=seguro)
    def cambiar_accion(aid: int, cambios: CambiosAccion):
        if not db.accion(aid):
            raise HTTPException(404, "no existe esa acción")
        return db.cambiar_accion(aid, **cambios.model_dump(exclude_unset=True))

    @app.post("/api/ediciones/{eid}/aplicar", dependencies=seguro)
    def aplicar_edicion(eid: int):
        """Aplica una corrección que Claude propuso por MCP. Si el turno cambió desde entonces, no se aplica a ciegas."""
        e = db.edicion(eid)
        if not e:
            raise HTTPException(404, "esa propuesta ya no existe")
        s = db.segmento(e["segmento_id"])
        if not s or s["texto"] != e["original"]:
            db.descartar_edicion(eid)
            raise HTTPException(409, "el turno cambió desde que Claude lo propuso: la propuesta se descartó")
        nuevo = db.editar_segmento(s["id"], e["propuesto"])
        db.descartar_edicion(eid)
        _reexportar(e["reunion_id"])
        return {"segmento": nuevo}

    @app.delete("/api/ediciones/{eid}", dependencies=seguro)
    def rechazar_edicion(eid: int):
        db.descartar_edicion(eid)
        return {"ok": True}

    @app.get("/api/brief", dependencies=seguro)
    async def proximo_brief():
        """La próxima reunión del calendario (en los próximos 15 min) con lo que quedó de la anterior de su serie."""
        return await asyncio.to_thread(brief.proximo)

    @app.post("/api/reuniones/{rid}/borrar-tramo", dependencies=seguro)
    def borrar_tramo(rid: str, segmentos: list[int] = Body(..., embed=True, min_length=1, max_length=2000)):
        """Saca de la reunión un tramo que no debió quedar: los turnos, las marcas de ese intervalo y el audio (las dos
        pistas quedan en silencio ahí). La app espera 10 s con «Deshacer» antes de llamar."""
        r = db.reunion_basica(rid)
        if not r:
            raise HTTPException(404, "no existe esa reunión")
        desde, hasta, n = db.borrar_segmentos(rid, segmentos)
        if not n:
            raise HTTPException(404, "esos turnos no son de esta reunión")
        silenciados = 0
        g = db.grabacion(r["grabacion_id"]) if r.get("grabacion_id") else None
        if g and desde is not None:
            try:
                silenciados = silenciar.silenciar(g["carpeta"], desde, hasta)
            except OSError as e:  # el texto ya se borró: igual se reexporta y la app avisa que el audio no
                logging.getLogger("minuta.api").warning("%s: no se pudo silenciar el audio: %s", rid, e)
        _reexportar(rid)
        return {"borrados": n, "desde": desde, "hasta": hasta, "audio": silenciados > 0}

    @app.post("/api/reuniones/{rid}/reemplazar", dependencies=seguro)
    def reemplazar(rid: str, original: str = Body(..., embed=True, min_length=1, max_length=200),
                   correcto: str = Body(..., embed=True, min_length=1, max_length=200)):
        if not db.reunion_basica(rid):
            raise HTTPException(404, "no existe esa reunión")
        n = db.reemplazar_en_reunion(rid, original, correcto)
        if n:
            _reexportar(rid)
        return {"cambiados": n}

    @app.delete("/api/reuniones/{rid}", dependencies=seguro)
    def borrar(rid: str):
        r = db.reunion(rid)
        if not r:
            raise HTTPException(404, "no existe esa reunión")
        if r.get("grabacion_id"):
            cola_mod.borrar_grabacion(r["grabacion_id"])
        else:
            db.borrar_reunion(rid)
        return {"borrada": rid}

    @app.get("/api/reuniones/{rid}/markdown", dependencies=seguro, response_class=PlainTextResponse)
    def markdown(rid: str):
        r = db.reunion(rid)
        if not r:
            raise HTTPException(404, "no existe esa reunión")
        nombres = db.nombres_de_turnos(r, {"mic": "Vos", "sistema": "Ellos", **(r.get("hablantes") or {})})
        turnos = []
        for s, quien in zip(r["segmentos"], nombres):
            hora = f" ({int(s['inicio'] // 60):02d}:{int(s['inicio'] % 60):02d})" if s.get("inicio") is not None else ""
            turnos.append((s.get("inicio"), f"**{quien}**{hora}: {s['texto']}"))
        cuerpo = db.intercalar_marcas(turnos, r["marcas"])
        resumen = []
        for sec in (r.get("resumen_minuta") or {}).get("secciones", []):
            vinetas = [f"- {v['texto']}" + (f" ({', '.join(_mmss(c) for c in v['citas'])})" if v["citas"] else "")
                       for v in sec["vinetas"]]
            resumen.append(f"## {sec['titulo']}\n\n" + "\n".join(vinetas))
        acciones = [a for a in r.get("acciones", []) if a["estado"] != "descartada"]
        if acciones:
            resumen.append("## Acciones\n\n" + "\n".join(
                f"- [{'x' if a['estado'] == 'aceptada' else ' '}] {a['texto']}"
                + (f" · {a['responsable']}" if a.get("responsable") else "") for a in acciones))
        if resumen:
            cuerpo = [*resumen, "## Transcripción", *cuerpo]
        return "\n\n".join([f"# {r['titulo']}", f"{r['inicio'][:16].replace('T', ' ')} · {r['carpeta'] or 'sin carpeta'}",
                            *cuerpo]) + "\n"

    @app.get("/api/buscar", dependencies=seguro)
    def buscar(q: str, prefijo: bool = False):
        """`prefijo`: la última palabra también encuentra lo que empieza así (Ctrl+K, mientras se escribe)."""
        return db.buscar(q[:200], prefijo=prefijo)

    # --- grabaciones ------------------------------------------------------------------------------------------

    @app.get("/api/grabaciones", dependencies=seguro)
    def grabaciones(estado: str | None = None):
        return db.grabaciones(estados=estado.split(",") if estado else None)

    @app.get("/api/grabaciones/{gid}/audio", dependencies=seguro)
    def audio(gid: str):
        g = db.grabacion(gid)
        if not g or not re.fullmatch(r"\d{8}-\d{6}", gid):
            raise HTTPException(404, "no existe esa grabación")
        carpeta = Path(g["carpeta"]).resolve()
        if config.GRABACIONES.resolve() not in carpeta.parents:
            raise HTTPException(404, "no existe esa grabación")
        destino = carpeta / mezcla.NOMBRE
        if not destino.exists():
            if not any(carpeta.glob("*.wav")):
                raise HTTPException(410, "el audio ya se borró por la retención configurada")
            mezcla.generar(carpeta)
        return FileResponse(destino, media_type="audio/ogg")

    @app.post("/api/grabaciones/{gid}/asignar", dependencies=seguro)
    def asignar(gid: str, titulo: str = Body(...), carpeta: str | None = Body(None)):
        g = db.grabacion(gid)
        if not g or not g.get("reunion_id"):
            raise HTTPException(404, "no existe esa grabación o todavía no se transcribió")
        if carpeta not in (*CARPETAS, None):
            raise HTTPException(422, "carpeta desconocida")
        db.editar_reunion(g["reunion_id"], titulo=titulo, carpeta=carpeta)
        db.actualizar_grabacion(gid, estado="archivada")
        cola_mod.exportar_reunion(g["reunion_id"])
        repaso = db.conexion().execute("SELECT 1 FROM trabajos WHERE tipo = 'repasar' AND grabacion_id = ? "
                                       "AND estado IN ('pendiente', 'corriendo')", (gid,)).fetchone()
        if not repaso and db.ajuste("resumir") and cola_mod.puede_salir(g, carpeta, True):
            db.encolar("resumir", gid)  # si hay un repaso pendiente, lo encola él al terminar
            if cola:
                cola.despertar()
        return db.reunion(g["reunion_id"])

    @app.post("/api/grabaciones/{gid}/reprocesar", dependencies=seguro)
    def reprocesar(gid: str, motor: str | None = Body(None, embed=True)):
        """Con `motor`, se fuerza ese motor; sin él (el «Reintentar» de un error), se usa la regla de la carpeta."""
        g = db.grabacion(gid)
        if not g:
            raise HTTPException(404, "no existe esa grabación")
        if motor is not None and motor not in MOTORES_VALIDOS:
            raise HTTPException(422, "motor desconocido")
        if motor is not None and not cola_mod.motor_permitido(motor, cola_mod.motores_de_grabacion(g)):
            raise HTTPException(409, "esta grabación no sale de la PC (sin identificar, «solo en esta PC» o carpeta "
                                     "local): solo se puede volver a transcribir en esta PC")
        if not any(Path(g["carpeta"]).glob("*.wav")):
            raise HTTPException(410, "el audio ya se borró: no se puede reprocesar")
        for f in Path(g["carpeta"]).glob("seg-*.json"):
            f.unlink()
        tid = db.encolar("transcribir", gid, **({"motor": motor} if motor else {}))
        if g["estado"] == "error":
            db.actualizar_grabacion(gid, estado="grabada", error=None)
        if cola:
            cola.despertar()
        return {"trabajo": tid}

    @app.delete("/api/grabaciones/{gid}", dependencies=seguro)
    def borrar_grabacion(gid: str):
        if not db.grabacion(gid):
            raise HTTPException(404, "no existe esa grabación")
        cola_mod.borrar_grabacion(gid)
        return {"borrada": gid}

    @app.get("/api/trabajos", dependencies=seguro)
    def trabajos():
        return db.trabajos()

    # --- motores, glosario, ajustes ---------------------------------------------------------------------------

    @app.get("/api/motores", dependencies=seguro)
    def motores():
        total, hoy = db.uso_por_motor(), db.uso_por_motor(desde=date.today().isoformat())
        aai = total.get("assemblyai", {})
        return {"configurados": secretos.estado(),
                "assemblyai": {"agotado": not nube.assemblyai_disponible() and bool(secretos.leer("assemblyai")),
                               "gastado_usd": round(aai.get("costo") or 0, 4),
                               "credito_estimado_usd": round(CREDITO_ASSEMBLYAI - (aai.get("costo") or 0), 2)},
                "groq": {"hoy_s": round((hoy.get("groq") or {}).get("segundos") or 0), "cuota_diaria_s": CUOTA_GROQ_S},
                "haiku": {"gastado_usd": round((total.get("haiku") or {}).get("costo") or 0, 4)},
                "local": {"segundos": round((total.get("local") or {}).get("segundos") or 0),
                          "enchufada": local.enchufada()}}

    @app.get("/api/glosario", dependencies=seguro)
    def glosario():
        prompt = config.glosario_prompt()
        return {"prompt": prompt, "terminos": config.glosario_terminos(), "tokens_prompt": _tokens(prompt),
                "limite_tokens": 223}

    @app.put("/api/glosario", dependencies=seguro)
    def guardar_glosario(prompt: str | None = Body(None), terminos: str | None = Body(None)):
        if prompt is not None:
            (config.GLOSARIO / "glosario-prompt.txt").write_text(prompt.strip() + "\n", encoding="utf-8")
        if terminos is not None:
            (config.GLOSARIO / "glosario-terminos.txt").write_text(terminos, encoding="utf-8")
        return glosario()

    @app.post("/api/glosario/deformacion", dependencies=seguro)
    def agregar_deformacion(correcto: str = Body(..., embed=True, min_length=2, max_length=200),
                            deformacion: str = Body(..., embed=True, min_length=2, max_length=200)):
        """Una corrección hecha a mano entra al glosario: la próxima reunión ya se corrige sola."""
        return {"linea": agregar_al_glosario(correcto, deformacion)}

    @app.get("/api/ajustes", dependencies=seguro)
    def ajustes():
        return {k: db.ajuste(k) for k in ("privacidad", "audio_dias", "exportar_a", "notificaciones", "diarizar",
                                          "diarizacion_auto", "resumir", "plantillas", "dictado", "dictado_motor")} | {
            "carpetas": CARPETAS, "secretos": secretos.estado()}

    @app.put("/api/ajustes", dependencies=seguro)
    def guardar_ajustes(cambios: dict = Body(...)):
        if "privacidad" in cambios:
            reglas = cambios["privacidad"]
            if not isinstance(reglas, dict) or any(not set(v) <= MOTORES_VALIDOS or not v for v in reglas.values()):
                raise HTTPException(422, "cada carpeta necesita al menos un motor válido")
            if reglas.get("sin identificar", ["local"]) != ["local"]:
                raise HTTPException(422, "lo no identificado como reunión se transcribe solo en local")
            db.guardar_ajuste("privacidad", reglas)
        if "audio_dias" in cambios:
            if not isinstance(cambios["audio_dias"], int) or not 0 <= cambios["audio_dias"] <= 365:
                raise HTTPException(422, "la retención va de 0 a 365 días")
            db.guardar_ajuste("audio_dias", cambios["audio_dias"])
        if "exportar_a" in cambios:
            db.guardar_ajuste("exportar_a", str(Path(cambios["exportar_a"])))
        if "plantillas" in cambios:
            p = cambios["plantillas"]
            if not isinstance(p, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 4000
                                              for k, v in p.items()):
                raise HTTPException(422, "cada plantilla es un texto de hasta 4000 caracteres")
            db.guardar_ajuste("plantillas", {k: v for k, v in p.items() if v.strip()})
        if "dictado_motor" in cambios:
            if cambios["dictado_motor"] not in ("groq", "local"):
                raise HTTPException(422, "el dictado va por groq o local")
            db.guardar_ajuste("dictado_motor", cambios["dictado_motor"])
        for clave in ("notificaciones", "diarizar", "diarizacion_auto", "resumir", "dictado"):
            if clave in cambios:
                if not isinstance(cambios[clave], bool):
                    raise HTTPException(422, f"{clave} es sí o no")
                db.guardar_ajuste(clave, cambios[clave])
        return ajustes()

    @app.put("/api/secretos", dependencies=seguro)
    def guardar_secretos(cambios: dict = Body(...)):
        for nombre in secretos.CLAVES_API:
            if nombre in cambios:
                secretos.guardar(nombre, (cambios[nombre] or "").strip())
        if "calendarios" in cambios:
            # Se agrega o se quita de a uno: {"acme": "https://…"} agrega o reemplaza, {"acme": null} quita.
            actuales = secretos.calendarios()
            for nombre, url in (cambios["calendarios"] or {}).items():
                if url is None:
                    actuales.pop(nombre, None)
                elif not str(url).startswith("https://"):
                    raise HTTPException(422, "las URL de calendario tienen que ser https")
                else:
                    actuales[nombre] = url
            secretos.guardar_calendarios(actuales)
        return secretos.estado()

    # --- eventos en vivo --------------------------------------------------------------------------------------

    @app.websocket("/api/eventos")
    async def eventos(ws: WebSocket):
        origen = ws.headers.get("origin")
        if (origen and origen not in ORIGENES) or not hmac.compare_digest(ws.query_params.get("t", "").encode(), token.encode()):
            await ws.close(code=4401)
            return
        await ws.accept()
        q = bus.suscribir() if bus else asyncio.Queue()

        async def esperar_cierre():
            # El canal solo escribe; sin leer, no se entera de que el cliente se fue o de que el motor se apaga,
            # y uvicorn se queda esperando este manejador para siempre.
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass

        cierre = asyncio.create_task(esperar_cierre())
        try:
            while not cierre.done():
                try:
                    mensaje = await asyncio.wait_for(q.get(), timeout=1.0)
                    await ws.send_text(mensaje)
                except asyncio.TimeoutError:
                    if vigia and vigia.grab is not None:  # niveles y tiempo de la grabación, una vez por segundo
                        # En un hilo: estado() espera el lock del vigía, que se toma largo al cerrar una grabación.
                        datos = await asyncio.to_thread(vigia.estado)
                        await ws.send_json({"tipo": "ahora", "datos": datos,
                                            "en": datetime.now().isoformat(timespec="seconds")})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            cierre.cancel()
            if bus:
                bus.desuscribir(q)

    return app


_tokenizador = []


def _tokens(texto):
    """Tokens de Whisper del prompt (el límite útil es ~223). Usa el tokenizador del modelo `small` en caché."""
    if not _tokenizador:
        try:
            import os
            from faster_whisper.utils import download_model
            from tokenizers import Tokenizer
            _tokenizador.append(Tokenizer.from_file(os.path.join(download_model("small", local_files_only=True), "tokenizer.json")))
        except Exception:
            _tokenizador.append(None)
    return len(_tokenizador[0].encode(texto).ids) if _tokenizador[0] else None
