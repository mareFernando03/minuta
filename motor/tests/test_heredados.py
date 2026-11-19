"""Tests del grabador. Correr con: python -m unittest discover -s tests (desde grabador-reuniones)."""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path


from minuta.datos.exportar import fecha_granola, meeting_raw
from minuta.clasificacion.calendarios import carpeta_para
from minuta.transcripcion.local import es_eco, fusionar

ARCHIVO_GRANOLA = Path.home() / "Documents" / "granola-archivo"


def seg(inicio, fin, texto):
    return {"inicio": inicio, "fin": fin, "texto": texto}


class Fusion(unittest.TestCase):
    def test_intercala_por_tiempo_y_etiqueta_como_granola(self):
        t = fusionar([seg(5, 8, "Yo hablo después.")], [seg(0, 4, "Ellos hablan primero.")])
        self.assertEqual(t, "System audio: Ellos hablan primero.\n\nMicrophone: Yo hablo después.")

    def test_une_segmentos_seguidos_del_mismo_canal(self):
        t = fusionar([], [seg(0, 2, "Uno."), seg(2.5, 4, "Dos.")])
        self.assertEqual(t, "System audio: Uno. Dos.")

    def test_no_une_si_hay_pausa_larga(self):
        t = fusionar([], [seg(0, 2, "Uno."), seg(10, 12, "Dos.")])
        self.assertEqual(t.count("System audio:"), 2)

    def test_descarta_el_eco_del_mic(self):
        sistema = [seg(10, 15, "La crisis económica europea y las empresas dejaron de hacer publicidad")]
        mic = [seg(10.4, 15.2, "la crisis económica europea las empresas dejaron de hacer publicidad")]
        self.assertTrue(es_eco(mic[0], sistema))
        self.assertNotIn("Microphone", fusionar(mic, sistema))

    def test_no_descarta_lo_que_digo_yo_al_mismo_tiempo(self):
        sistema = [seg(10, 15, "La crisis económica europea y las empresas dejaron de hacer publicidad")]
        self.assertFalse(es_eco(seg(11, 13, "Perdón, ¿podés repetir lo del archivo J?"), sistema))

    def test_no_descarta_la_misma_frase_en_otro_momento(self):
        self.assertFalse(es_eco(seg(100, 103, "sí, de acuerdo"), [seg(10, 13, "sí, de acuerdo")]))


class Repeticiones(unittest.TestCase):
    def test_colapsa_el_bucle_de_whisper(self):
        from minuta.transcripcion.local import limpiar_repeticiones
        self.assertEqual(limpiar_repeticiones("mencionó Contoso y cen- " + "cen- " * 120 + "P-FAB"),
                         "mencionó Contoso y cen- P-FAB")

    def test_respeta_repeticiones_normales(self):
        from minuta.transcripcion.local import limpiar_repeticiones
        self.assertEqual(limpiar_repeticiones("Sí, sí, sí. Tal cual. Tal cual."), "Sí, sí, sí. Tal cual. Tal cual.")


class Correccion(unittest.TestCase):
    def test_extiende_un_cambio_de_palabra_a_toda_la_reunion(self):
        from minuta.correccion.glosario import aplicar
        t, _ = aplicar("no cambiaste el upsetting. Al final del upsetting.",
                       [{"original": "Al final del upsetting", "correcto": "Al final del appsettings"}])
        self.assertEqual(t, "no cambiaste el appsettings. Al final del appsettings.")

    def test_no_toca_palabras_que_solo_la_contienen(self):
        from minuta.correccion.glosario import aplicar
        t, _ = aplicar("el MIT y el MITO", [{"original": "el MIT y", "correcto": "el Meet y"}])
        self.assertEqual(t, "el Meet y el MITO")

    def test_ignora_lo_que_no_esta_en_el_texto(self):
        from minuta.correccion.glosario import aplicar
        t, aplicados = aplicar("hola", [{"original": "chau Banana", "correcto": "chau Fer"}])
        self.assertEqual((t, aplicados), ("hola", []))


class Motores(unittest.TestCase):
    def test_tiempos_vuelven_al_reloj_de_la_grabacion(self):
        from minuta.transcripcion.nube import a_tiempo_real
        # dos tramos de voz: 10-12 s y 40-45 s de la grabación, pegados con 0,3 s de pausa
        mapa = [(0.0, 10.0, 2.0), (2.3, 40.0, 5.0)]
        self.assertEqual(a_tiempo_real(1.0, mapa), 11.0)
        self.assertEqual(a_tiempo_real(3.3, mapa), 41.0)

    def test_frases_por_pausa_y_punto(self):
        from minuta.transcripcion.nube import _frases
        segs = _frases([("Hola.", 0.0, 0.4), ("¿Todo", 1.0, 1.3), ("bien?", 1.3, 1.6), ("Sí", 4.0, 4.2)])
        self.assertEqual([s["texto"] for s in segs], ["Hola.", "¿Todo bien?", "Sí"])



class Clasificacion(unittest.TestCase):
    def test_casos_conocidos(self):
        casos = {
            "Seguimiento": "Seguimiento Northwind",
            "SEGUIMIENTO PROYECTO Fabrikam": "Contoso Fabrikam",
            "Infra Contoso/Initech": "Contoso Fabrikam",
            "Daily northwind-equipo-desarrollo": "Seguimiento Northwind",
            "Acme All Hands": "Acme",
            "Generación de archivos txt": "Orbit",
            "Reunión Hooli - Globex": "Globex",
        }
        for titulo, esperado in casos.items():
            self.assertEqual(carpeta_para(titulo), esperado, titulo)

    def test_huddle_de_slack_toma_el_canal_con_parlante(self):
        from minuta.clasificacion.calendarios import clasificar
        titulos = {"Pedro Ríos - Acme - Slack": 22,
                   "northwind-equipo-desarrollo (Canal) - Acme - Slack [Principal] 🏠🔊": 15,
                   "(1) Chat | Tomás Rafael Giménez (Familiar externo) | Microsoft Teams - Google Chrome": 25}
        import minuta.clasificacion.calendarios as c
        original, c.eventos = c.eventos, lambda i, f, forzar=False: []
        try:
            r = clasificar(datetime(2026, 10, 9, 17, 29), datetime(2026, 10, 9, 17, 40), titulos, ["slack.exe"])
        finally:
            c.eventos = original
        self.assertEqual(r["titulo"], "Huddle de Slack: northwind-equipo-desarrollo")
        self.assertEqual(r["folders"], ["Seguimiento Northwind"])

    def test_un_chat_de_teams_no_es_una_reunion(self):
        from minuta.clasificacion.calendarios import VENTANA_REUNION
        self.assertIsNone(VENTANA_REUNION.match("(1) Chat | Tomás Rafael Giménez | Microsoft Teams"))
        self.assertIsNotNone(VENTANA_REUNION.match("Reunión de seguimiento | Microsoft Teams"))
        self.assertIsNotNone(VENTANA_REUNION.match("Meet - hsg-arja-mtx"))

    def _clasificar_sin_calendario(self, titulos, inicio):
        import minuta.clasificacion.calendarios as c
        original, c.eventos = c.eventos, lambda i, f, forzar=False: []
        try:
            return c.clasificar(inicio, inicio + timedelta(minutes=10), titulos, ["chrome.exe"])
        finally:
            c.eventos = original

    def test_meet_con_dos_puntos(self):
        # La del 28/10: Chrome mostró «Meet: Seguimiento» y quedó sin título ni carpeta.
        r = self._clasificar_sin_calendario({"Meet: Seguimiento - Google Chrome": 15,
                                             "Chat | BDD - Contoso/fabrikam | Microsoft Teams - Google Chrome": 3},
                                            datetime(2026, 10, 28, 9, 23))
        self.assertEqual(r["titulo"], "Seguimiento")
        self.assertEqual(r["folders"], ["Seguimiento Northwind"])

    def test_llamada_desde_el_chat_de_teams(self):
        # La del 27/10: una llamada desde el chat, que Teams muestra en la vista compacta. El chat solo sigue sin contar.
        r = self._clasificar_sin_calendario(
            {"Chat | Vista compacta de la reunión | Lucía Acosta Romero, Juan Castro | Microsoft Teams - Google Chrome": 1,
             "Chat | Lucía Acosta Romero, Juan Castro | Microsoft Teams - Google Chrome": 21},
            datetime(2026, 10, 27, 13, 48))
        self.assertEqual(r["titulo"], "Llamada de Teams: Lucía Acosta Romero, Juan Castro")

    def test_teams_en_espanol_sin_la_seccion_y_edge_cuenta(self):
        import minuta.clasificacion.calendarios as c
        titulos = {"Equipos y canales | Sync_ Archivos Altas (Externo) | Microsoft Teams - Microsoft Edge": 12,
                   "Reunión de seguimiento | Microsoft Teams - Google Chrome": 3}
        original, c.eventos = c.eventos, lambda i, f, forzar=False: []
        try:
            r = c.clasificar(datetime(2026, 10, 13, 13, 10), datetime(2026, 10, 13, 13, 17), titulos, ["msedge.exe"])
        finally:
            c.eventos = original
        self.assertEqual(r["titulo"], "Sync_ Archivos Altas (Externo)")
        self.assertEqual(r["origen"], "ventana")

    def test_sin_evento_rebaja_el_calendario_una_vez(self):
        import minuta.clasificacion.calendarios as c
        pedidos = []
        evento = {"titulo": "Sync: Archivos Altas", "inicio": datetime(2026, 10, 13, 13, 0), "fin": datetime(2026, 10, 13, 13, 30),
                  "calendario": "northwind_outlook", "participantes": [{"mail": "lmartinez@northwind.example", "nombre": "Laura"}],
                  "solape_s": 420}

        def eventos(i, f, forzar=False):
            pedidos.append(forzar)
            return [evento] if forzar else []  # la invitación recién aparece al rebajar la copia
        original, c.eventos = c.eventos, eventos
        try:
            r = c.clasificar(datetime(2026, 10, 13, 13, 10), datetime(2026, 10, 13, 13, 17), {}, ["msedge.exe"])
        finally:
            c.eventos = original
        self.assertEqual(pedidos, [False, True])
        self.assertEqual(r["titulo"], "Sync: Archivos Altas")
        self.assertEqual(r["folders"], ["Contoso Fabrikam"])  # el calendario de Northwind es el de Contoso

    def test_dos_eventos_a_la_misma_hora_desempata_la_ventana(self):
        # 19/10: la Fabrikam y «Organizar Northwind» estaban las dos a las 10:30; ganaba la primera del calendario
        import minuta.clasificacion.calendarios as c
        base = {"inicio": datetime(2026, 10, 19, 10, 30), "fin": datetime(2026, 10, 19, 11, 30), "solape_s": 1908}
        otra = dict(base, titulo="Organizar Northwind por vacaciones Pedro", calendario="acme", participantes=[])
        fabrikam = dict(base, titulo="SEGUIMIENTO PROYECTO Fabrikam", calendario="northwind_outlook",
                    participantes=[{"mail": "lmartinez@northwind.example", "nombre": "Laura"}])
        titulos = {"Equipos y canales | SEGUIMIENTO PROYECTO Fabrikam (Externo) | Microsoft Teams - Google Chrome": 12,
                   "Nerdearla - Gemini Notebook - Google Chrome": 67}
        original, c.eventos = c.eventos, lambda i, f, forzar=False: [otra, fabrikam]
        try:
            r = c.clasificar(datetime(2026, 10, 19, 10, 30), datetime(2026, 10, 19, 11, 3), titulos, ["chrome.exe"])
            sin_ventana = c.clasificar(datetime(2026, 10, 19, 10, 30), datetime(2026, 10, 19, 11, 3), {}, ["chrome.exe"])
        finally:
            c.eventos = original
        self.assertEqual(r["titulo"], "SEGUIMIENTO PROYECTO Fabrikam")
        self.assertEqual(r["folders"], ["Contoso Fabrikam"])
        self.assertEqual(sin_ventana["titulo"], "Organizar Northwind por vacaciones Pedro")  # sin pistas, como antes

    def test_la_copia_reciente_no_se_rebaja_al_forzar(self):
        import time
        import minuta.clasificacion.calendarios as c
        from minuta import config
        config.CACHE_CALENDARIOS.mkdir(parents=True, exist_ok=True)
        ruta = config.CACHE_CALENDARIOS / "prueba.ics"
        ruta.write_text("BEGIN:VCALENDAR" + chr(10) + "END:VCALENDAR" + chr(10), encoding="ascii")
        bajadas = []
        original = c.urllib.request.urlopen
        c.urllib.request.urlopen = lambda url, timeout: bajadas.append(url) or (_ for _ in ()).throw(OSError("sin red"))
        c._intento.pop("prueba", None)
        try:
            c._ics("prueba", "https://x/1.ics", forzar=True)  # 0 s de edad: no se rebaja
            self.assertEqual(bajadas, [])
            viejo = time.time() - c.FORZAR_DESPUES_S - 5
            import os
            os.utime(ruta, (viejo, viejo))
            c._ics("prueba", "https://x/1.ics", forzar=True)
            self.assertEqual(bajadas, ["https://x/1.ics"])
        finally:
            c.urllib.request.urlopen = original

    def test_mi_dominio_no_hace_acme_a_todo(self):
        self.assertIsNone(carpeta_para("Revisión de arquitectura yo@acme.example"))



class Esquema(unittest.TestCase):
    def test_fecha_en_el_formato_que_lee_archivar(self):
        self.assertEqual(fecha_granola(datetime(2026, 10, 2, 9, 0)), "Oct 2, 2026 9:00 AM GMT-3")
        self.assertEqual(fecha_granola(datetime(2026, 10, 8, 15, 30)), "Oct 8, 2026 3:30 PM GMT-3")
        self.assertEqual(fecha_granola(datetime(2026, 10, 8, 0, 5)), "Oct 8, 2026 12:05 AM GMT-3")

    def test_participantes_sin_los_que_rechazaron(self):
        raw = meeting_raw([{"mail": "a@x.com", "nombre": "Ana", "estado": "ACCEPTED"},
                           {"mail": "b@x.com", "nombre": "Beto", "estado": "DECLINED"}])
        self.assertIn("Ana &lt;a@x.com&gt;", raw)
        self.assertNotIn("Beto", raw)
        self.assertIn("<summary>", raw)

    @unittest.skipUnless(ARCHIVO_GRANOLA.exists(), "sin archivo de Granola")
    def test_mismas_claves_que_un_json_de_granola(self):
        import tempfile
        from minuta.datos.exportar import archivar
        real = json.loads(next(ARCHIVO_GRANOLA.glob("2026-*.json")).read_text(encoding="utf-8"))
        tmp = Path(tempfile.mkdtemp())
        _, ruta = archivar(tmp, datetime(2026, 10, 9, 9, 0), "Seguimiento", ["Seguimiento Northwind"], [], "Microphone: hola")
        nuevo = json.loads(ruta.read_text(encoding="utf-8"))
        self.assertEqual(set(nuevo), set(real) - {"transcript_error"})
        indice = json.loads((tmp / "indice.json").read_text(encoding="utf-8"))
        self.assertEqual(set(indice[0]), {"archivo", "title", "date", "folders", "transcript_chars"})


if __name__ == "__main__":
    unittest.main()


class Ajeno(unittest.TestCase):
    """Lo que entra bajo por el mic en una llamada no es del usuario (medido el 13/10/2026)."""

    def setUp(self):
        import numpy as np
        # 20 s de mic: los primeros 10 con voz cerca (0,05), los otros 10 con la oficina de fondo (0,005).
        self.env = np.array([0.05] * 100 + [0.005] * 100, dtype=np.float32)

    def palabras(self, prob):
        return [["x", 0, 1, prob]]

    def test_lo_bajo_y_dudoso_se_descarta_y_lo_cercano_queda(self):
        from minuta.transcripcion.local import fusionar_turnos
        mic = [dict(seg(1, 4, "Bien, yo quería saber"), palabras=self.palabras(0.98)),
               dict(seg(12, 18, "te llevo a pasar en cocina"), palabras=self.palabras(0.69))]
        t = fusionar_turnos(mic, [], voz_mic=self.env)
        self.assertEqual([x["texto"] for x in t], ["Bien, yo quería saber"])

    def test_lo_bajo_se_descarta_aunque_el_motor_lo_oiga_claro(self):
        """El sync de Altas con AssemblyAI: Juan, al lado, con confianza 0,99."""
        from minuta.transcripcion.local import fusionar_turnos
        mic = [dict(seg(12, 15, "Puede pasar, puede ser que hagamos pruebas"), palabras=self.palabras(0.99))]
        self.assertEqual(fusionar_turnos(mic, [], voz_mic=self.env), [])

    def test_sin_confianza_tambien_decide_la_energia(self):
        from minuta.transcripcion.local import fusionar_turnos
        mic = [seg(12, 15, "Son productos anuales"), seg(2, 3, "Chau.")]
        self.assertEqual([x["texto"] for x in fusionar_turnos(mic, [], voz_mic=self.env)], ["Chau."])

    def test_sin_envolvente_no_se_filtra_por_energia(self):
        """Grabación a mano: las voces del cuarto entran bajas y son la reunión."""
        from minuta.transcripcion.local import fusionar_turnos
        self.assertEqual(len(fusionar_turnos([seg(12, 15, "Son productos anuales")], [])), 1)

    def test_aplausos_se_descarta_siempre(self):
        from minuta.transcripcion.local import fusionar_turnos
        self.assertEqual(fusionar_turnos([seg(1, 2, "Aplausos."), seg(5, 6, "Aplausos, sí, pero hablé")], []),
                         [{"canal": "mic", "inicio": 5, "fin": 6, "texto": "Aplausos, sí, pero hablé", "palabras": None}])

    def test_fuera_del_audio_no_se_filtra(self):
        from minuta.transcripcion.local import voz_del_tramo
        self.assertIsNone(voz_del_tramo(self.env, 500, 510))


class Envolvente(unittest.TestCase):
    def test_bloques_alineados_en_tramos_de_un_decimo(self):
        import tempfile
        import wave
        import numpy as np
        from minuta.transcripcion.local import envolvente
        with tempfile.TemporaryDirectory() as d:
            for n, amp in ((0, 1000), (1, 0)):
                with wave.open(str(Path(d) / f"mic-{n:03d}.wav"), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                    w.writeframes(np.full(16000 * 300, amp, "<i2").tobytes())
            env = envolvente(d, "mic")
        self.assertEqual(len(env), 6000)
        self.assertAlmostEqual(float(env[2999]), 1000 / 32768, places=4)
        self.assertEqual(float(env[3000]), 0.0)
        self.assertIsNone(envolvente(d, "mic"))  # la carpeta ya no existe: sin audio



class AjenoPorTurno(unittest.TestCase):
    def test_una_frase_baja_al_final_de_un_turno_del_usuario_se_conserva(self):
        import numpy as np
        from minuta.transcripcion.local import fusionar_turnos
        env = np.array([0.05] * 30 + [0.005] * 30, dtype=np.float32)  # habla fuerte 3 s y cierra bajo
        mic = [seg(0, 3, "Lo que digo fuerte"), seg(3.2, 5, "y bueno, eso.")]
        t = fusionar_turnos(mic, [], voz_mic=env)
        self.assertEqual([x["texto"] for x in t], ["Lo que digo fuerte y bueno, eso."])
