"""El seguidor de salida: cuándo la grabación pasa a la salida por la que de verdad suena la reunión."""
from minuta.captura import grabador as g

SUENA, CALLA = 0.05, 0.0


def revisar_cada_2s(seguidor, grabada, sondas, segundos, t0=100.0):
    """Revisa como `_seguir_dispositivos`, cada 2 s, y devuelve el primer cambio que pide (con su instante) o None."""
    for i in range(int(segundos // 2) + 1):
        t = t0 + 2 * i
        destino = seguidor.revisar(grabada, sondas, t)
        if destino:
            return destino, t - t0
    return None


def test_pasa_a_la_salida_que_suena_con_la_grabada_muda():
    # El caso del 22/10: Meet por los auriculares, la salida por defecto en los altavoces.
    cambio = revisar_cada_2s(g.SeguidorDeSalida(), CALLA, {"Auriculares": SUENA}, 20)
    assert cambio == ("Auriculares", g.CAMBIO_S)


def test_con_audio_en_las_dos_no_cambia():
    assert revisar_cada_2s(g.SeguidorDeSalida(), SUENA, {"Auriculares": SUENA}, 60) is None


def test_un_sonido_corto_en_otra_salida_no_alcanza():
    s = g.SeguidorDeSalida()
    assert s.revisar(CALLA, {"Auriculares": SUENA}, 100.0) is None
    assert s.revisar(CALLA, {"Auriculares": SUENA}, 102.0) is None
    assert s.revisar(CALLA, {"Auriculares": CALLA}, 104.0) is None  # la notificación terminó
    assert s.revisar(CALLA, {"Auriculares": SUENA}, 106.0) is None  # vuelve a contar desde acá
    assert s.revisar(CALLA, {"Auriculares": SUENA}, 106.0 + g.CAMBIO_S) == "Auriculares"


def test_por_debajo_del_umbral_no_cuenta_como_sonando():
    assert revisar_cada_2s(g.SeguidorDeSalida(), CALLA, {"Auriculares": g.SUENA_RMS / 2}, 60) is None


def test_con_varias_sonando_elige_la_mas_fuerte():
    cambio = revisar_cada_2s(g.SeguidorDeSalida(), CALLA, {"TV": 0.01, "Auriculares": SUENA}, 20)
    assert cambio[0] == "Auriculares"


def test_despues_de_cambiar_empieza_de_cero():
    s = g.SeguidorDeSalida()
    assert revisar_cada_2s(s, CALLA, {"Auriculares": SUENA}, 20)
    assert s.revisar(CALLA, {"Altavoces": SUENA}, 200.0) is None  # una sola revisión no alcanza para volver


def test_sin_sufijo_deja_el_nombre_de_la_salida():
    assert g.sin_sufijo("Auriculares (Realtek(R) Audio) [Loopback]") == "Auriculares (Realtek(R) Audio)"
