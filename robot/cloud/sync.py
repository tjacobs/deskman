#!.venv/bin/python

# Upload new robot recordings to the Neon recordings bucket, then write the index the web page reads

# Imports
import argparse
import json
import os
import sys
import time
import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROBOT_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_RECORDINGS_DIR = os.path.join(ROBOT_DIR, "recordings")
DEFAULT_ENV_FILE = os.path.join(SCRIPT_DIR, ".env.local")

# Config bucket
DEFAULT_BUCKET = "recordings"
INDEX_KEY = "index.json"
VIDEO_SUFFIX = ".mp4"
VIDEO_CONTENT_TYPE = "video/mp4"
INDEX_CONTENT_TYPE = "application/json"

# Videos never change once named by their start time, the index changes on every sync
VIDEO_CACHE_CONTROL = "public, max-age=31536000, immutable"
INDEX_CACHE_CONTROL = "no-cache"

# Skip broken stubs, and files the recorder may still be writing
MIN_VIDEO_BYTES = 10000
SETTLE_SECONDS = 15

# Upload in parts so a big recording survives a slow link, and print progress this often
PART_BYTES = 8 * 1024 * 1024
PROGRESS_STEP_PERCENT = 10
BYTES_PER_MEGABYTE = 1024 * 1024

# Main
def main():
    args = parse_args()
    load_env(args.env)
    client = make_client()
    remote = list_remote(client, args.bucket)
    uploaded = upload_new(client, args.bucket, args.load, remote, args.dry_run)
    if not args.dry_run:
        save_index(client, args.bucket, remote)
    print(f"Synced, {uploaded} uploaded, {len(remote)} in the bucket.")

# Parse args
def parse_args():
    parser = argparse.ArgumentParser(description="Upload new recordings to the Neon bucket and refresh its index.")
    parser.add_argument("--load", default=DEFAULT_RECORDINGS_DIR, help="Folder of recordings to upload")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Bucket to upload into")
    parser.add_argument("--env", default=DEFAULT_ENV_FILE, help="Env file with the Neon S3 keys")
    parser.add_argument("--dry-run", action="store_true", help="Say what would upload without uploading")
    return parser.parse_args()

# Read KEY=VALUE lines into the environment, leaving anything already set alone
def load_env(path):
    if not os.path.isfile(path):
        sys.exit(f"No env file at {path}, run neon link from the repo first.")
    with open(path) as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# Build an S3 client on the branch endpoint, Neon needs path style addressing
def make_client():
    endpoint = os.environ.get("AWS_ENDPOINT_URL_S3")
    if not endpoint:
        sys.exit("AWS_ENDPOINT_URL_S3 is not set, check the env file.")
    return boto3.client("s3", endpoint_url=endpoint, config=Config(s3={"addressing_style": "path"}))

# Map each video already in the bucket to its size
def list_remote(client, bucket):
    remote = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            if item["Key"].endswith(VIDEO_SUFFIX):
                remote[item["Key"]] = item["Size"]
    return remote

# Upload each finished recording the bucket lacks, or has at a different size
def upload_new(client, bucket, folder, remote, dry_run):
    uploaded = 0
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not name.endswith(VIDEO_SUFFIX) or not os.path.isfile(path):
            continue

        # Leave stubs and recordings still being written for a later sync
        size = os.path.getsize(path)
        if size < MIN_VIDEO_BYTES:
            continue
        if time.time() - os.path.getmtime(path) < SETTLE_SECONDS:
            print(f"Skipping {name}, still recording.")
            continue
        if remote.get(name) == size:
            continue

        # Say what would go up, or send it
        if dry_run:
            print(f"Would upload {name}, {size / BYTES_PER_MEGABYTE:.1f} MB.")
            continue
        upload_video(client, bucket, path, name, size)
        remote[name] = size
        uploaded += 1
    return uploaded

# Send one video in parts, printing progress as it goes
def upload_video(client, bucket, path, name, size):
    print(f"Uploading {name}, {size / BYTES_PER_MEGABYTE:.1f} MB.", flush=True)
    start = time.time()
    progress = {"sent": 0, "shown": 0}

    # Print each time another step of the file has gone up
    def note_progress(sent_bytes):
        progress["sent"] += sent_bytes
        percent = progress["sent"] * 100 // size
        if percent >= progress["shown"] + PROGRESS_STEP_PERCENT:
            progress["shown"] = percent - percent % PROGRESS_STEP_PERCENT
            print(f"  {progress['shown']}%", flush=True)

    # Upload with the type and caching a browser needs to play it
    transfer = TransferConfig(multipart_threshold=PART_BYTES, multipart_chunksize=PART_BYTES)
    extra = {"ContentType": VIDEO_CONTENT_TYPE, "CacheControl": VIDEO_CACHE_CONTROL}
    client.upload_file(path, bucket, name, ExtraArgs=extra, Config=transfer, Callback=note_progress)
    seconds = max(time.time() - start, 1)
    print(f"  Done in {seconds:.0f} sec, {size / BYTES_PER_MEGABYTE / seconds:.1f} MB/s.", flush=True)

# Write the list of videos, newest first, with the public link to each
def save_index(client, bucket, remote):
    base = os.environ["AWS_ENDPOINT_URL_S3"].rstrip("/") + "/" + bucket + "/"
    videos = [{"key": key, "bytes": remote[key], "url": base + key} for key in sorted(remote, reverse=True)]
    body = json.dumps({"videos": videos}, indent=2)
    client.put_object(Bucket=bucket, Key=INDEX_KEY, Body=body.encode("utf-8"), ContentType=INDEX_CONTENT_TYPE, CacheControl=INDEX_CACHE_CONTROL)

# Main
if __name__ == "__main__":
    main()
