"""Monta o manifesto de segmentos de fundo a partir de uma playlist do YouTube.

Roda fora do pipeline, à mão, e é o único lugar que fala com a playlist. O
resultado é um JSON no bucket: uma lista de segmentos de `segment_seconds`, cada
um com a fonte e o offset de onde recortá-lo. **Nenhum vídeo é baixado aqui** —
a leitura é `extract_flat`, só metadados, e o clipe só existe como arquivo
quando um render o sorteia (ver `background_source.py`).

    python scripts/build_background_manifest.py <url-da-playlist> [--dry-run]

Reconstruir é seguro e barato: a chave de cada segmento é função de
`(video_id, start)`, então um manifesto novo continua citando os clipes que já
estão materializados no bucket, e eles não precisam ser rebaixados.

⚠️ Rodar de novo com outro `segment_seconds` **não** invalida o cache, mas gera
chaves novas: os clipes antigos deixam de ser citados e viram lixo no bucket até
alguém apagá-los à mão, porque o despejo LRU só considera o que está no
manifesto corrente.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import settings  # noqa: E402
from src.orchestrator.backgrounds import plan_segments, segment_key  # noqa: E402
from src.orchestrator.storage.client import upload_bytes  # noqa: E402


def le_playlist(url: str) -> list[dict]:
    from yt_dlp import YoutubeDL

    with YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}) as ydl:
        info = ydl.extract_info(url, download=False)
    return [e for e in (info.get("entries") or []) if e and e.get("id")]


def monta(entradas: list[dict], prefix: str, segundos: int, min_tail: int) -> dict:
    clips, sem_duracao, curtos = [], 0, 0
    for e in entradas:
        duracao = e.get("duration")
        if not duracao:
            # Sem duração no metadata não dá para planejar os cortes, e chutar
            # produziria segmentos que terminam depois do fim do vídeo.
            sem_duracao += 1
            continue
        starts = plan_segments(float(duracao), segundos, min_tail)
        if not starts:
            curtos += 1
            continue
        for start in starts:
            clips.append(
                {
                    "key": segment_key(prefix, str(e["id"]), start),
                    "video_id": str(e["id"]),
                    "start": start,
                }
            )
    return {
        "version": 1,
        "segment_seconds": segundos,
        "clips": clips,
        "_stats": {
            "videos": len(entradas),
            "descartados_sem_duracao": sem_duracao,
            "descartados_curtos": curtos,
        },
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--dry-run", action="store_true", help="não sobe, só imprime o resumo")
    args = ap.parse_args()

    cfg = settings.CONFIG
    prefix = str(cfg.template.background_prefix)
    destino = str(cfg.template.background_manifest)
    segundos = int(cfg.backgrounds.segment_seconds)
    min_tail = int(cfg.backgrounds.min_tail_seconds)

    entradas = le_playlist(args.url)
    manifesto = monta(entradas, prefix, segundos, min_tail)
    s = manifesto["_stats"]

    print(f"vídeos na playlist: {s['videos']}")
    print(f"  descartados por não ter duração: {s['descartados_sem_duracao']}")
    print(f"  descartados por serem curtos demais (<{min_tail}s): {s['descartados_curtos']}")
    print(f"segmentos de {segundos}s: {len(manifesto['clips'])}")
    if manifesto["clips"]:
        print(f"exemplo: {manifesto['clips'][0]['key']}")

    if args.dry_run:
        print("\n--dry-run: nada subiu")
        return

    await upload_bytes(
        cfg.storage.bucket,
        destino,
        json.dumps(manifesto, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )
    print(f"\nmanifesto em {destino}")


if __name__ == "__main__":
    asyncio.run(main())
