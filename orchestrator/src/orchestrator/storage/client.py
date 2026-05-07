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
