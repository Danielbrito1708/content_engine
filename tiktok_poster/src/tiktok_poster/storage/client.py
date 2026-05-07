import asyncio

import boto3
from botocore.client import Config

from src.core import settings


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.env.minio_endpoint,
        aws_access_key_id=settings.env.minio_access_key,
        aws_secret_access_key=settings.env.minio_secret_key,
        config=Config(signature_version="s3v4"),
    )


async def generate_presigned_url(bucket: str, key: str, ttl_seconds: int) -> str:
    public_base = settings.env.r2_public_url.rstrip("/")
    if public_base:
        return f"{public_base}/{key}"
    s3 = _get_s3_client()
    url: str = await asyncio.to_thread(
        s3.generate_presigned_url,
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )
    return url
