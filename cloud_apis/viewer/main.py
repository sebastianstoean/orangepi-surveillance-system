from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import storage


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEFAULT_SEGMENT_LIMIT = 20
REQUEST_TIMEOUT_SECONDS = 10

app = FastAPI(title="Surveillance Viewer")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
http = requests.Session()


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def encryption_key() -> bytes:
    key_hex = required_env("ENCRYPT_KEY")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise RuntimeError("ENCRYPT_KEY must be a 32-byte hex string") from exc
    if len(key) != 32:
        raise RuntimeError("ENCRYPT_KEY must decode to exactly 32 bytes")
    return key


def decrypt_frame(encrypted_base64: str, key: bytes) -> str:
    encrypted = base64.b64decode(encrypted_base64)
    if len(encrypted) <= AES.block_size:
        raise ValueError("Encrypted frame is missing IV or ciphertext")

    iv = encrypted[: AES.block_size]
    ciphertext = encrypted[AES.block_size :]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    jpeg_bytes = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return base64.b64encode(jpeg_bytes).decode("ascii")


def segment_sort_key(blob_name: str) -> datetime:
    timestamp = PurePosixPath(blob_name).stem
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def status_viewing_url() -> str:
    raw_url = required_env("STATUS_SERVICE_URL").rstrip("/")
    if raw_url.endswith("/status/viewing"):
        return raw_url
    return f"{raw_url}/status/viewing"


def segments_file_path() -> str | None:
    return os.getenv("SEGMENTS_FILE")


def decrypted_segment_from_payload(
    payload: dict[str, Any],
    key: bytes,
) -> dict[str, Any]:
    timestamp = str(payload["timestamp"])
    encrypted_frames = payload.get("frames", [])
    decrypted_frames = [decrypt_frame(str(frame), key) for frame in encrypted_frames]
    return {"timestamp": timestamp, "frames": decrypted_frames}


def read_latest_local_segment(file_path: str, key: bytes) -> list[dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        return []

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [decrypted_segment_from_payload(payload, key)]


def read_gcs_segments(limit: int, key: bytes) -> list[dict[str, Any]]:
    storage_client = storage.Client()
    bucket = storage_client.bucket(required_env("SEGMENTS_BUCKET"))
    blobs = list(bucket.list_blobs(prefix="segments/"))
    latest_blobs = sorted(blobs, key=lambda blob: segment_sort_key(blob.name), reverse=True)[
        :limit
    ]

    segments: list[dict[str, Any]] = []
    for blob in latest_blobs:
        try:
            payload = json.loads(blob.download_as_text())
            segments.append(decrypted_segment_from_payload(payload, key))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read or decrypt segment {blob.name}: {exc}",
            ) from exc

    return segments


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/frames")
def frames(
    limit: int = Query(
        default=int(os.getenv("DEFAULT_SEGMENT_LIMIT", str(DEFAULT_SEGMENT_LIMIT))),
        ge=1,
        le=100,
    )
) -> dict[str, list[dict[str, Any]]]:
    key = encryption_key()

    local_file = segments_file_path()
    if local_file:
        try:
            return {"segments": read_latest_local_segment(local_file, key)}
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read or decrypt latest segment: {exc}",
            ) from exc

    return {"segments": read_gcs_segments(limit, key)}


@app.post("/viewing")
def viewing() -> dict[str, bool]:
    try:
        response = http.post(status_viewing_url(), timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not update viewer status: {exc}",
        ) from exc
    return {"updated": True}
