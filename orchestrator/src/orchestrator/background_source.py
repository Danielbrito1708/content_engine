"""Materializa um clipe de fundo sob demanda, a partir da fonte no manifesto.

A biblioteca de fundos passou de dezenas de arquivos para ~1.500 segmentos
cortados de uma playlist de 55 horas. Baixar tudo custaria em torno de 100 GB
num servidor com 175 GB livres, para usar três clipes por dia — então o clipe é
buscado no render em que for sorteado, e não antes.

Duas medidas de 29/08/2026 é que tornam isso barato:

1. **O yt-dlp baixa só a faixa pedida** (``download_ranges``). Um segmento de
   2 min de um vídeo de 9 min custa o trecho, não o vídeo. ⚠️ Isso vale
   *enquanto* ``force_keyframes_at_cuts`` ficar desligado: com ele o yt-dlp
   baixa o arquivo inteiro para cortar com precisão de frame — medido, 188 MB e
   subindo para um trecho de 2 min. Precisão de frame não vale nada num fundo.
2. **A fonte tem de ser 4K.** O recorte 9:16 de um 3840x2160 dá 1215x2160 e
   *desce* para 1080x1920; de um 1080p daria 607x1080 e *subiria*, borrado. Daí
   o formato pedir ``height>=2160`` antes de aceitar menos.

O corte é central. Medido num frame de gameplay em 29/08/2026: câmera de
terceira pessoa mantém o veículo no centro por construção, então o recorte não
perde a ação — e o céu liso em cima e a rampa embaixo são justamente onde o card
e a legenda entram, o que ajuda a legibilidade em vez de brigar com ela.
"""

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import func, select
from structlog import get_logger

from src.orchestrator.db.models import PipelinePart
from src.orchestrator.storage.client import delete_keys, object_exists, upload_file

log = get_logger(__name__)


class MaterializeError(RuntimeError):
    """O clipe não pôde ser produzido. Quem chama decide se degrada ou falha."""


def _ydl_opts(dest: Path, start: int, seconds: int, quality: str) -> dict:
    return {
        "format": quality,
        "outtmpl": str(dest / "fonte.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # Ver a nota 1 no topo: nada de `force_keyframes_at_cuts` aqui.
        "download_ranges": lambda *_: [
            {"start_time": start, "end_time": start + seconds}
        ],
    }


def _baixa_trecho(video_id: str, start: int, seconds: int, dest: Path, quality: str) -> Path:
    from yt_dlp import YoutubeDL

    with YoutubeDL(_ydl_opts(dest, start, seconds, quality)) as ydl:
        ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)

    arquivos = [p for p in dest.iterdir() if p.stem == "fonte" and p.suffix != ".part"]
    if not arquivos:
        raise MaterializeError(f"yt-dlp terminou sem gravar arquivo para {video_id}@{start}")
    return arquivos[0]


def _para_vertical(entrada: Path, saida: Path, filtro: str, crf: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(entrada),
            "-an",
            "-vf", filtro,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-g", "60", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
            str(saida),
        ],
        check=True,
        capture_output=True,
    )
    if not saida.exists() or saida.stat().st_size == 0:
        raise MaterializeError(f"ffmpeg terminou sem produzir {saida.name}")


def _produz(video_id: str, start: int, seconds: int, filtro: str, quality: str, crf: int) -> bytes:
    tmp = Path(tempfile.mkdtemp(prefix="bgsrc-"))
    try:
        fonte = _baixa_trecho(video_id, start, seconds, tmp, quality)
        saida = tmp / "vertical.mp4"
        _para_vertical(fonte, saida, filtro, crf)
        return saida.read_bytes()
    finally:
        # O trecho em 4K passa de 400 MB. Deixar isso no disco entre renders é o
        # que a materialização sob demanda existe para evitar.
        shutil.rmtree(tmp, ignore_errors=True)


async def ensure_available(
    key: str,
    entry: dict,
    *,
    bucket: str,
    segment_seconds: int,
    video_filter: str,
    quality: str,
    crf: int,
) -> bool:
    """Garante que ``key`` existe no bucket. Devolve se precisou materializar.

    Idempotente por construção: a primeira coisa que faz é perguntar se a chave
    já está lá. Dois runs que sorteiem o mesmo clipe em paralelo produzem o mesmo
    objeto duas vezes no pior caso, o que é desperdício e não corrupção — o
    conteúdo é função de ``(video_id, start)``.
    """
    if await object_exists(bucket, key):
        return False

    video_id = str(entry.get("video_id") or "")
    start = int(entry.get("start") or 0)
    if not video_id:
        raise MaterializeError(f"entrada de manifesto sem video_id para {key}")

    log.info("materializing background", key=key, video_id=video_id, start=start)
    dados = await asyncio.get_event_loop().run_in_executor(
        None, _produz, video_id, start, segment_seconds, video_filter, quality, crf
    )

    tmp = Path(tempfile.mkdtemp(prefix="bgup-"))
    try:
        local = tmp / "vertical.mp4"
        local.write_bytes(dados)
        await upload_file(bucket, key, str(local))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    log.info("background materialized", key=key, mb=round(len(dados) / 1e6, 1))
    return True


async def evict(session, *, bucket: str, materializados: list[str], teto: int) -> list[str]:
    """Apaga os clipes materializados menos recentemente usados, até caber no teto.

    LRU e não FIFO porque a rotação já garante que um clipe usado não volta tão
    cedo: o menos recentemente usado é também o que mais demora a ser pedido de
    novo, então apagá-lo é o despejo que menos provavelmente será desfeito.

    ⚠️ **Nunca despeja clipe de parte que ainda não renderizou.** Uma parte com
    ``background_key`` gravado e ``video_key`` nulo está esperando o render, e o
    blender_worker vai buscar essa chave no bucket — apagá-la trocaria o freio de
    espaço por um render quebrado. Materialização é barata; render perdido não.
    """
    if len(materializados) <= teto:
        return []

    ultimo_uso = dict(
        (
            await session.execute(
                select(PipelinePart.background_key, func.max(PipelinePart.created_at))
                .where(PipelinePart.background_key.is_not(None))
                .group_by(PipelinePart.background_key)
            )
        ).all()
    )
    pendentes = {
        k
        for (k,) in (
            await session.execute(
                select(PipelinePart.background_key).where(
                    PipelinePart.background_key.is_not(None),
                    PipelinePart.video_key.is_(None),
                )
            )
        ).all()
    }

    # `k in ultimo_uso` exclui o recém-materializado, que ainda não tem parte
    # nenhuma apontando para ele: ele é o clipe do render que está acontecendo
    # agora, e despejá-lo apagaria o fundo que acabou de ser buscado.
    candidatos = [k for k in materializados if k not in pendentes and k in ultimo_uso]
    candidatos.sort(key=lambda k: ultimo_uso[k])

    excedente = len(materializados) - teto
    alvo = candidatos[:excedente]
    if not alvo:
        log.warning(
            "cache de fundo acima do teto e nada despejável",
            materializados=len(materializados),
            teto=teto,
            pendentes=len(pendentes),
        )
        return []

    await delete_keys(bucket, alvo)
    log.info("background cache evicted", apagados=len(alvo), restam=len(materializados) - len(alvo))
    return alvo
