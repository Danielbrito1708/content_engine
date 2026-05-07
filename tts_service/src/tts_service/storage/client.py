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


async def upload_audio(bucket: str, key: str, data: bytes) -> None:
    s3 = get_s3_client()
    await asyncio.to_thread(
        s3.put_object,
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType="audio/mpeg",
    )
