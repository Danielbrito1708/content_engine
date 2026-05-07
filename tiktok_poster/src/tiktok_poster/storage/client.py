import asyncio
import os

import boto3
from botocore.client import Config


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
    )


async def generate_presigned_url(bucket: str, key: str, ttl_seconds: int) -> str:
    public_base = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
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
