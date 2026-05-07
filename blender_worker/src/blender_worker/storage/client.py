import asyncio
import os

import boto3
from botocore.client import Config


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
    )


async def download_file(bucket: str, key: str, dest_path: str) -> None:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    await asyncio.to_thread(get_s3_client().download_file, bucket, key, dest_path)


async def upload_file(bucket: str, key: str, src_path: str) -> None:
    await asyncio.to_thread(get_s3_client().upload_file, src_path, bucket, key)
