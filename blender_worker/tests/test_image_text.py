import pytest
from PIL import ImageFont

from src.blender_worker.image.text import TextBlock, measure, wrap_text


@pytest.fixture(scope="module")
def font() -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=20)


def test_short_text_stays_single_line(font):
    lines = wrap_text("Olá", font, max_width=1000)
    assert lines == ["Olá"]


def test_long_text_breaks_into_multiple_lines(font):
    text = "Este é um comentário bem longo que deve ser quebrado em várias linhas automaticamente"
    lines = wrap_text(text, font, max_width=200)
    assert len(lines) > 1


def test_each_line_fits_within_max_width(font):
    text = "palavra1 palavra2 palavra3 palavra4 palavra5 palavra6 palavra7 palavra8"
    max_width = 150
    lines = wrap_text(text, font, max_width=max_width)
    for line in lines:
        assert font.getlength(line) <= max_width


def test_empty_text_returns_single_empty_line(font):
    lines = wrap_text("", font, max_width=200)
    assert lines == [""]


def test_single_word_longer_than_max_width_is_not_dropped(font):
    # palavra maior que max_width não pode ser perdida
    lines = wrap_text("supercalifragilisticexpialidocious", font, max_width=10)
    assert len(lines) == 1
    assert "supercalifragilisticexpialidocious" in lines[0]


def test_newlines_in_text_create_separate_paragraphs(font):
    lines = wrap_text("linha um\nlinha dois", font, max_width=1000)
    assert len(lines) == 2
    assert lines[0] == "linha um"
    assert lines[1] == "linha dois"


def test_measure_returns_textblock(font):
    block = measure("Olá mundo", font, max_width=1000)
    assert isinstance(block, TextBlock)
    assert block.lines == ["Olá mundo"]
    assert block.line_height > 0
    assert block.total_height == block.line_height


def test_measure_total_height_grows_with_more_lines(font):
    short = measure("Oi", font, max_width=1000)
    long = measure(
        "Este comentário é longo o suficiente para ocupar várias linhas no layout final",
        font,
        max_width=120,
    )
    assert long.total_height > short.total_height


def test_measure_total_height_includes_spacing(font):
    block = measure("linha um\nlinha dois\nlinha três", font, max_width=1000, line_spacing=8)
    assert block.total_height == block.line_height * 3 + 8 * 2


def test_measure_single_line_has_no_spacing(font):
    block = measure("linha única", font, max_width=1000, line_spacing=10)
    assert block.total_height == block.line_height
