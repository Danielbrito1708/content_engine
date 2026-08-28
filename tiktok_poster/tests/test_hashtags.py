from src.tiktok_poster.hashtags.selector import compose_caption, select_hashtags

MANDATORY = ["#tiktokbrasil", "#fyp"]
POOL = ["#viral", "#drama", "#relacionamento", "#suspense", "#historia"]


def test_mandatory_always_included():
    result = select_hashtags([], MANDATORY, POOL, max_total=8)
    assert "#tiktokbrasil" in result
    assert "#fyp" in result


def test_hints_added_after_mandatory():
    hints = ["#traição", "#amor"]
    result = select_hashtags(hints, MANDATORY, POOL, max_total=8)
    assert "#traição" in result
    assert "#amor" in result
    assert result.index("#tiktokbrasil") < result.index("#traição")


def test_deduplication():
    hints = ["#tiktokbrasil", "#drama"]
    result = select_hashtags(hints, MANDATORY, POOL, max_total=8)
    assert result.count("#tiktokbrasil") == 1


def test_max_total_respected():
    hints = ["#h1", "#h2", "#h3", "#h4", "#h5", "#h6"]
    result = select_hashtags(hints, MANDATORY, POOL, max_total=5)
    assert len(result) == 5


def test_pool_fills_remaining_slots():
    result = select_hashtags([], MANDATORY, POOL, max_total=5)
    assert len(result) == 5
    assert "#viral" in result


def test_adds_hash_prefix_if_missing():
    result = select_hashtags(["drama"], ["tiktokbrasil"], [], max_total=5)
    assert "#tiktokbrasil" in result
    assert "#drama" in result


def test_compose_caption_single_part():
    tags = ["#fyp", "#drama"]
    caption = compose_caption("Segue para mais! 🔥", tags, part_number=1, total_parts=1)
    assert "Segue para mais! 🔥" in caption
    assert "#fyp" in caption
    assert "Parte" not in caption


def test_compose_caption_multi_part():
    tags = ["#fyp"]
    caption = compose_caption("Comenta 👇", tags, part_number=1, total_parts=2)
    assert "Parte 1/2" in caption
    assert "Comenta 👇" in caption


def test_compose_caption_part2():
    tags = ["#fyp"]
    caption = compose_caption("Segue 🔥", tags, part_number=2, total_parts=2)
    assert "Parte 2/2" in caption


def test_pool_order_varies_between_posts():
    """A cauda da legenda não pode ser a mesma post após post.

    Sem seed, o preenchimento vinha sempre do topo do pool, então todo post que
    não tinha hints suficientes terminava com as mesmas tags na mesma ordem —
    assinatura de conta automatizada, que é o que isto existe para parar."""
    pool = [f"#tag{i}" for i in range(30)]
    caudas = {
        tuple(select_hashtags([], [], pool, 8, seed=f"tiktok:serie-{i}:1")) for i in range(20)
    }
    assert len(caudas) >= 18


def test_pool_order_is_reproducible():
    """Reagendar a mesma parte tem de devolver a mesma legenda, ou um retry
    publica um texto diferente do que foi revisado."""
    pool = [f"#tag{i}" for i in range(30)]
    primeira = select_hashtags([], [], pool, 8, seed="tiktok:serie-a:1")
    assert select_hashtags([], [], pool, 8, seed="tiktok:serie-a:1") == primeira


def test_tiktok_and_youtube_seeds_diverge():
    """O mesmo vídeo nos dois destinos não pode sair com a mesma cauda."""
    pool = [f"#tag{i}" for i in range(30)]
    tiktok = select_hashtags([], [], pool, 8, seed="tiktok:serie-a:1")
    youtube = select_hashtags([], [], pool, 8, seed="youtube:serie-a:1")
    assert tiktok != youtube


def test_seed_does_not_reorder_mandatory_or_hints():
    """A ordem embaralhada é só do pool: obrigatórias são escolha explícita e
    hints descrevem a história, os dois na ordem em que chegam."""
    resultado = select_hashtags(
        ["#gancho", "#drama"], ["#fixa1", "#fixa2"], ["#p1", "#p2"], 8, seed="tiktok:x:1"
    )
    assert resultado[:4] == ["#fixa1", "#fixa2", "#gancho", "#drama"]


def test_no_mandatory_still_produces_a_caption():
    """`mandatory` vazio é a configuração de produção desde 28/08/2026."""
    resultado = select_hashtags(["#drama"], [], ["#p1", "#p2", "#p3"], 4, seed="tiktok:x:1")
    assert resultado[0] == "#drama"
    assert len(resultado) == 4
