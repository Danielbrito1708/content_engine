"""Apaga `outputs/{job_id}/` do R2 mais velhos que `[storage] output_retention_days`.

O vídeo publicado já está no TikTok/YouTube; o que fica em `outputs/` depois
disso é histórico e matéria-prima para reedição, não o produto. Decisão de
30/08/2026, adiada desde 14/08 — ver docs/deploy.md → "O que continua em
aberto". `outputs/` cresce sem nada apagar hoje: era 91% dos 12,9 GB do bucket
quando a decisão foi tomada, contra um orçamento de 10 GB.

A idade vem do `LastModified` do objeto no R2, não de uma tabela do banco —
`final.mp4` e `output.blend` são escritos juntos, no fim do render
(worker.py `output_key`/`blend_key`), então a data de upload já é a data em
que o vídeo ficou pronto para publicar. Não correlaciona com "foi publicado
de fato": um job que falhou depois do render (ex. Buffer recusou) expira do
mesmo jeito, porque não há reedição possível para um vídeo que nunca saiu.

    python scripts/retention.py [--dry-run] [--days N]

Roda fora do pipeline, via cron — mesmo modelo que o `pg_dump` planejado no
mesmo TODO.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boto3  # noqa: E402
from structlog import get_logger  # noqa: E402

from src.core import settings  # noqa: E402

log = get_logger(__name__)


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=settings.env.minio_endpoint,
        aws_access_key_id=settings.env.minio_access_key,
        aws_secret_access_key=settings.env.minio_secret_key,
        region_name="us-east-1",
    )


def _expired_keys(client, bucket: str, cutoff: datetime) -> list[str]:
    """Toda chave sob `outputs/` com `LastModified` antes de `cutoff`.

    Um job inteiro (mp4 + blend) some junto: os dois nascem no mesmo upload,
    então checar cada objeto contra o corte, em vez de agrupar por job antes,
    dá o mesmo resultado sem precisar casar os dois arquivos por `job_id`.
    """
    expired = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="outputs/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff:
                expired.append(obj["Key"])
    return expired


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--days", type=int, default=None,
        help="Sobrepõe [storage] output_retention_days do config.ini",
    )
    args = parser.parse_args()

    days = args.days if args.days is not None else int(settings.CONFIG.storage.output_retention_days)
    bucket = settings.CONFIG.storage.bucket
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    client = _s3()
    keys = _expired_keys(client, bucket, cutoff)

    if not keys:
        log.info("retention: nada a apagar", days=days, cutoff=cutoff.isoformat())
        return

    total = len(keys)
    if args.dry_run:
        log.info("retention: dry-run", would_delete=total, days=days, cutoff=cutoff.isoformat())
        for key in keys:
            print(key)
        return

    for i in range(0, total, 1000):
        lote = keys[i : i + 1000]
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in lote]})

    log.info("retention: apagado", deleted=total, days=days, cutoff=cutoff.isoformat())


if __name__ == "__main__":
    main()
