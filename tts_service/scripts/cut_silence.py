#!/usr/bin/env python
"""Corta os silêncios de um arquivo de áudio, fora do pipeline.

Usa o mesmo `process_audio` que o `POST /generate` aplica antes do upload ao
MinIO — o objetivo é calibrar os limiares contra um áudio real sem precisar
subir o serviço nem reprocessar um pipeline run inteiro.

    poetry run python scripts/cut_silence.py narracao.mp3
    poetry run python scripts/cut_silence.py narracao.mp3 --max-pause-ms 150 --thresh-db -35
    poetry run python scripts/cut_silence.py parte_*.mp3 --dry-run

A normalização de loudness fica **ligada por padrão**, como em produção: o que sai
daqui é o que o pipeline produziria. `--no-normalize` isola o efeito do corte
quando se quer ouvir só ele.

A saída é sempre MP3 (o encoder é escolhido pela extensão que `process_audio`
usa no temp interno), independente do formato de entrada.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tts_service.audio.postprocess import process_audio  # noqa: E402

DEFAULT_SUFFIX = ".trimmed.mp3"


@dataclass
class CutResult:
    source: Path
    output: Path | None
    before_ms: int
    after_ms: int

    @property
    def removed_ms(self) -> int:
        return self.before_ms - self.after_ms

    @property
    def removed_pct(self) -> float:
        return (self.removed_ms / self.before_ms * 100) if self.before_ms else 0.0


def probe_duration_ms(audio_bytes: bytes) -> int:
    """Duração em ms via ffprobe. Devolve 0 quando o ffprobe não consegue ler."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True,
            check=True,
            text=True,
        )
        return int(float(json.loads(result.stdout)["format"]["duration"]) * 1000)
    except (subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError):
        return 0
    finally:
        os.unlink(path)


def default_output_for(source: Path) -> Path:
    return source.with_suffix("").with_name(source.stem + DEFAULT_SUFFIX)


def cut_file(
    source: Path,
    output: Path | None,
    max_pause_ms: int,
    silence_thresh_db: int,
    normalize: bool = True,
) -> CutResult:
    """Processa um arquivo. `output=None` roda tudo mas não escreve (dry-run)."""
    original = source.read_bytes()
    trimmed = process_audio(
        original,
        trim_silence=True,
        max_pause_ms=max_pause_ms,
        silence_thresh_db=silence_thresh_db,
        normalize=normalize,
    )
    if output is not None:
        output.write_bytes(trimmed)
    return CutResult(
        source=source,
        output=output,
        before_ms=probe_duration_ms(original),
        after_ms=probe_duration_ms(trimmed),
    )


def _fmt_ms(ms: int) -> str:
    return f"{ms / 1000:.2f}s"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cut_silence",
        description="Remove silêncios do início, do fim e internos de um áudio de narração.",
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="arquivo(s) de áudio de entrada")
    parser.add_argument(
        "-o", "--output", type=Path,
        help=f"caminho de saída (só com um input; padrão: <nome>{DEFAULT_SUFFIX})",
    )
    parser.add_argument(
        "--max-pause-ms", type=int, default=200,
        help="teto de cada pausa interna; pausas menores passam intactas (padrão: 200)",
    )
    parser.add_argument(
        "--thresh-db", type=int, default=-40, dest="silence_thresh_db",
        help="nível em dB abaixo do qual é considerado silêncio (padrão: -40)",
    )
    parser.add_argument(
        "--no-normalize", action="store_true",
        help="desliga o loudnorm; isola o efeito do corte de silêncio",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="processa e reporta quanto seria cortado, sem escrever arquivo",
    )
    parser.add_argument(
        "-f", "--force", action="store_true",
        help="sobrescreve o arquivo de saída se ele já existir",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.output and len(args.inputs) > 1:
        print("erro: --output só vale com um único input", file=sys.stderr)
        return 2

    missing = [p for p in args.inputs if not p.is_file()]
    if missing:
        for p in missing:
            print(f"erro: arquivo não encontrado: {p}", file=sys.stderr)
        return 2

    failures = 0
    for source in args.inputs:
        output = None
        if not args.dry_run:
            output = args.output or default_output_for(source)
            if output.resolve() == source.resolve():
                print(f"erro: saída sobrescreveria a entrada: {source}", file=sys.stderr)
                failures += 1
                continue
            if output.exists() and not args.force:
                print(f"erro: {output} já existe (use --force)", file=sys.stderr)
                failures += 1
                continue

        try:
            result = cut_file(
                source, output,
                args.max_pause_ms, args.silence_thresh_db,
                normalize=not args.no_normalize,
            )
        except FileNotFoundError:
            print("erro: ffmpeg não encontrado no PATH", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            detail = stderr[-1] if stderr else "sem detalhe"
            print(f"erro: ffmpeg falhou em {source}: {detail}", file=sys.stderr)
            failures += 1
            continue

        destination = "(dry-run)" if output is None else str(output)
        # ASCII arrow on purpose: the console codepage on Windows (cp1252) can't
        # encode "→" and print() would raise UnicodeEncodeError mid-run.
        print(
            f"{source}: {_fmt_ms(result.before_ms)} -> {_fmt_ms(result.after_ms)} "
            f"(-{_fmt_ms(result.removed_ms)}, {result.removed_pct:.1f}%) {destination}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
