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
