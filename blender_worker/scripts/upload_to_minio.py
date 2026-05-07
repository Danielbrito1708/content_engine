"""
Usage: python scripts/upload_to_minio.py <local_path> <minio_key>
Example: python scripts/upload_to_minio.py template.blend templates/template.blend
"""
import os
import sys
import boto3
from botocore.client import Config

ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://127.0.0.1:9000")
ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
BUCKET = os.environ.get("MINIO_BUCKET", "blender-jobs")


def get_client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


def ensure_bucket(client):
    existing = [b["Name"] for b in client.list_buckets()["Buckets"]]
    if BUCKET not in existing:
        client.create_bucket(Bucket=BUCKET)
        print(f"Bucket '{BUCKET}' criado.")


def upload(local_path, key):
    client = get_client()
    ensure_bucket(client)
    client.upload_file(local_path, BUCKET, key)
    print(f"Upload OK: {local_path} -> {BUCKET}/{key}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python scripts/upload_to_minio.py <arquivo_local> <chave_minio>")
        sys.exit(1)
    upload(sys.argv[1], sys.argv[2])
