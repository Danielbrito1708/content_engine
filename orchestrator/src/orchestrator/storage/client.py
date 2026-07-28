import asyncio

import boto3

from src.core import settings


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=settings.env.minio_endpoint,
        aws_access_key_id=settings.env.minio_access_key,
        aws_secret_access_key=settings.env.minio_secret_key,
        region_name="us-east-1",
    )


async def upload_bytes(bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    client = _s3()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type),
    )


async def list_keys(bucket: str, prefix: str) -> list[str]:
    """Every object under ``prefix``, sorted, directory placeholders excluded.

    Sorted because the background rotation indexes into this list: a stable order
    is what makes the choice reproducible when the same part is rendered twice.
    """
    client = _s3()
    loop = asyncio.get_event_loop()

    def _list() -> list[str]:
        keys: list[str] = []
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys.extend(
                obj["Key"] for obj in page.get("Contents", []) if not obj["Key"].endswith("/")
            )
        return sorted(keys)

    return await loop.run_in_executor(None, _list)
