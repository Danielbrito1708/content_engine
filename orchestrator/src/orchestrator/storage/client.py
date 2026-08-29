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


async def object_exists(bucket: str, key: str) -> bool:
    """Se a chave já está no bucket.

    É o que decide se um clipe precisa ser materializado ou já está lá. Usa
    ``head_object`` e não ``list_keys`` porque a pergunta é sobre **uma** chave:
    listar um prefixo de milhares de segmentos para responder isso custaria uma
    página inteira por render.
    """
    client = _s3()
    loop = asyncio.get_event_loop()

    def _head() -> bool:
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    return await loop.run_in_executor(None, _head)


async def upload_file(bucket: str, key: str, path: str, content_type: str = "video/mp4") -> None:
    """Sobe um arquivo do disco. Separado de ``upload_bytes`` porque um clipe de
    fundo tem dezenas de MB e carregá-lo inteiro na memória não tem motivo."""
    client = _s3()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: client.upload_file(path, bucket, key, ExtraArgs={"ContentType": content_type}),
    )


async def get_bytes(bucket: str, key: str) -> bytes:
    client = _s3()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: client.get_object(Bucket=bucket, Key=key)["Body"].read()
    )


async def delete_keys(bucket: str, keys: list[str]) -> None:
    """Apaga em lotes de 1000, que é o teto do ``delete_objects`` da API S3."""
    if not keys:
        return
    client = _s3()
    loop = asyncio.get_event_loop()

    def _delete() -> None:
        for i in range(0, len(keys), 1000):
            lote = keys[i : i + 1000]
            client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in lote]})

    await loop.run_in_executor(None, _delete)
