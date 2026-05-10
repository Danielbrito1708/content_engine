import asyncio

import boto3
from botocore.client import Config

from src.core import settings


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.env.minio_endpoint,
        aws_access_key_id=settings.env.minio_access_key,
        aws_secret_access_key=settings.env.minio_secret_key,
        config=Config(signature_version="s3v4"),
    )


async def upload_audio(bucket: str, key: str, data: bytes) -> None:
    await _put(bucket, key, data, "audio/mpeg")


async def upload_bytes(bucket: str, key: str, data: bytes, content_type: str) -> None:
    await _put(bucket, key, data, content_type)


async def _put(bucket: str, key: str, data: bytes, content_type: str) -> None:
    s3 = get_s3_client()
    await asyncio.to_thread(
        s3.put_object,
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
