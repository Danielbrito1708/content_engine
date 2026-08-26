"""A janela de publicação é uma decisão de produto, não um detalhe de config.

Os horários vivem em `config.ini` em UTC, mas quem assiste está no Brasil
(UTC-3). Um teste que lê o arquivo é o que impede a próxima edição de mover um
slot para fora da janela sem ninguém perceber — o erro não aparece em lugar
nenhum, o vídeo só sai de madrugada.
"""

import configparser
import os
from datetime import datetime, time, timedelta, timezone

BRT = timezone(timedelta(hours=-3))

#: Janela pedida, em horário de Brasília.
WINDOW_START = time(11, 0)
WINDOW_END = time(20, 0)


def _posting_section() -> configparser.SectionProxy:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.ini")
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return parser["posting"]


def _preferred_times_brt() -> list[time]:
    raw = _posting_section()["preferred_times"]
    times = []
    for item in raw.split(","):
        h, m = item.strip().split(":")
        utc = datetime(2026, 1, 1, int(h), int(m), tzinfo=timezone.utc)
        times.append(utc.astimezone(BRT).time())
    return times


def test_preferred_times_dentro_da_janela():
    for slot in _preferred_times_brt():
        assert WINDOW_START <= slot <= WINDOW_END, f"{slot} está fora de 11h–20h (BRT)"


def test_um_slot_por_horario():
    times = _preferred_times_brt()
    assert len(times) == len(set(times))


def test_ha_slots_para_o_posts_per_day():
    """Menos horários que `posts_per_day` deixaria a cota diária inalcançável."""
    section = _posting_section()
    assert len(_preferred_times_brt()) >= int(section["posts_per_day"])


def test_continuacao_do_ultimo_slot_ainda_cabe_na_janela():
    """A parte 2 pendura no horário da parte 1 e ignora os `preferred_times`.

    Se o último slot do dia encostar no fim da janela, a continuação sai fora
    dela — por isso o último horário guarda pelo menos um `series_gap_minutes`
    de folga.
    """
    section = _posting_section()
    gap = timedelta(minutes=int(section["series_gap_minutes"]))
    last = max(_preferred_times_brt())
    continuation = (datetime.combine(datetime(2026, 1, 1).date(), last) + gap).time()
    assert continuation <= WINDOW_END
