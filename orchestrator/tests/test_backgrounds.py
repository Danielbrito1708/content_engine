import uuid

import pytest

from src.orchestrator.backgrounds import pick_background

CLIPS = [f"assets/backgrounds/bg_{i:03d}.mp4" for i in range(40)]


def _rodar_ciclo(n: int, clips=CLIPS) -> list[str]:
    """Escolhe `n` clipes em sequência, alimentando de volta o que já saiu —
    que é o que o worker faz ao gravar `background_key` antes do render."""
    usados: dict[str, int] = {}
    escolhidos = []
    for i in range(n):
        clipe = pick_background(clips, str(uuid.UUID(int=i)), 1, usados)
        usados[clipe] = usados.get(clipe, 0) + 1
        escolhidos.append(clipe)
    return escolhidos


def test_always_returns_one_of_the_clips():
    for part in range(1, 6):
        assert pick_background(CLIPS, "run-a", part) in CLIPS


def test_is_deterministic():
    """A re-render has to reuse the same footage, or a retry silently
    produces a different video than the one already reviewed."""
    first = pick_background(CLIPS, "run-a", 2)
    assert pick_background(CLIPS, "run-a", 2) == first


def test_parts_of_the_same_run_mostly_differ():
    """Parts of one run go out back-to-back, which is where repeated footage
    would be most obvious. The seed includes the part number precisely so they
    spread out."""
    runs = [str(uuid.UUID(int=i)) for i in range(100)]
    differing = sum(
        1 for run in runs if pick_background(CLIPS, run, 1) != pick_background(CLIPS, run, 2)
    )
    assert differing >= 90


def test_spreads_across_the_library():
    chosen = {pick_background(CLIPS, str(uuid.UUID(int=i)), 1) for i in range(200)}
    assert len(chosen) > len(CLIPS) / 2


def test_single_clip_library_still_works():
    assert pick_background(["only.mp4"], "run-a", 1) == "only.mp4"


def test_empty_library_raises():
    with pytest.raises(ValueError):
        pick_background([], "run-a", 1)


# --- a rotação de verdade -------------------------------------------------


def test_library_is_exhausted_before_anything_repeats():
    """O ponto da mudança. O picker antigo era um sorteio com reposição: em 47
    posts sobre 40 clipes ele reusou um clipe 20 vezes, a primeira repetição no
    segundo dia. Um ciclo tem de passar pela biblioteca inteira antes de voltar."""
    escolhidos = _rodar_ciclo(len(CLIPS))
    assert len(set(escolhidos)) == len(CLIPS)


def test_second_cycle_also_covers_the_whole_library():
    """A contagem é acumulada, então o segundo ciclo só começa quando o
    primeiro fecha — e cobre a biblioteca de novo, não um pedaço dela."""
    escolhidos = _rodar_ciclo(2 * len(CLIPS))
    assert sorted(set(escolhidos)) == sorted(CLIPS)
    assert all(escolhidos.count(clipe) == 2 for clipe in CLIPS)


def test_a_new_clip_jumps_the_queue():
    """Clipe adicionado depois começa com zero usos, então sai antes do que já
    está em rotação — footage nova chega ao canal sem esperar o ciclo fechar."""
    usados = {clipe: 1 for clipe in CLIPS}
    novo = "assets/backgrounds/bg_999.mp4"
    assert pick_background([*CLIPS, novo], "run-a", 1, usados) == novo


def test_a_removed_clip_does_not_block_the_cycle():
    """Contagem de um clipe que saiu do bucket não pode influenciar a escolha:
    a rotação só olha as chaves que existem hoje."""
    usados = {"assets/backgrounds/bg_apagado.mp4": 99}
    assert pick_background(CLIPS, "run-a", 1, usados) in CLIPS


def test_ties_are_not_broken_by_list_order():
    """Empate resolvido por ordem da lista faria um ciclo novo caminhar a
    biblioteca alfabeticamente, e posts consecutivos dividiriam trechos
    consecutivos do mesmo arquivo de origem."""
    inicios = {_rodar_ciclo(3)[0] for _ in range(1)}
    assert inicios != {CLIPS[0]}


def test_no_usage_map_keeps_the_old_behaviour():
    """Sem contagem — a primeira parte depois da migration, com a tabela toda
    nula — a escolha continua sendo a de antes, não um erro."""
    assert pick_background(CLIPS, "run-a", 1, {}) == pick_background(CLIPS, "run-a", 1)
