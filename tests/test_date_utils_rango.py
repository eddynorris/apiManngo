from datetime import UTC, date, datetime

from utils.date_utils import rango_utc_de_dias_peru


def test_rango_de_un_dia_peru_en_utc():
    inicio, fin = rango_utc_de_dias_peru(date(2026, 11, 13), date(2026, 11, 13))
    # Lima es UTC-5 todo el año (sin horario de verano)
    assert inicio == datetime(2026, 11, 13, 5, 0, tzinfo=UTC)
    assert fin == datetime(2026, 11, 14, 5, 0, tzinfo=UTC)


def test_noche_de_lima_cae_en_su_dia_aunque_en_utc_ya_sea_manana():
    inicio, fin = rango_utc_de_dias_peru(date(2026, 11, 13), date(2026, 11, 13))
    las_2330_lima = datetime(2026, 11, 14, 4, 30, tzinfo=UTC)
    medianoche_lima = datetime(2026, 11, 14, 5, 0, tzinfo=UTC)
    assert inicio <= las_2330_lima < fin
    assert not (inicio <= medianoche_lima < fin)


def test_rango_de_varios_dias_es_inclusivo():
    inicio, fin = rango_utc_de_dias_peru(date(2026, 10, 1), date(2026, 10, 31))
    assert inicio == datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
    assert fin == datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
