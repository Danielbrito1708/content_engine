import asyncio
import os

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


async def download_file(bucket: str, key: str, dest_path: str) -> None:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    await asyncio.to_thread(get_s3_client().download_file, bucket, key, dest_path)


async def upload_file(bucket: str, key: str, src_path: str) -> None:
    await asyncio.to_thread(get_s3_client().upload_file, src_path, bucket, key)
