from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageFont


@dataclass(frozen=True)
class TextBlock:
    lines: list[str]
    line_height: int
    line_spacing: int

    @property
    def total_height(self) -> int:
        if not self.lines:
            return 0
        return self.line_height * len(self.lines) + self.line_spacing * max(0, len(self.lines) - 1)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []

    for paragraph in (text.splitlines() or [""]):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue

        current: list[str] = []
        for word in words:
            candidate = " ".join(current + [word])
            if font.getlength(candidate) <= max_width:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]

        if current:
            lines.append(" ".join(current))

    return lines


def measure(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    line_spacing: int = 4,
) -> TextBlock:
    lines = wrap_text(text, font, max_width)
    ascent, descent = font.getmetrics()
    return TextBlock(lines=lines, line_height=ascent + descent, line_spacing=line_spacing)
