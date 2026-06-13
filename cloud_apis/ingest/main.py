from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, status
from google.cloud import storage
from pydantic import BaseModel, Field, field_validator


app = FastAPI(title="Surveillance Ingest API")


class SegmentUpload(BaseModel):
    timestamp: str
    segment_duration_seconds: int = Field(gt=0)
    frames: list[str] = Field(min_length=1)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        parse_iso_timestamp(value)
        return value


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_iso_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalized_object_name(timestamp: str) -> str:
    parsed = parse_iso_timestamp(timestamp)
    normalized = parsed.isoformat().replace("+00:00", "Z")
    return f"segments/{normalized}.json"


def segments_file_path() -> str | None:
    return os.getenv("SEGMENTS_FILE")


def write_latest_local_segment(payload: SegmentUpload, file_path: str) -> None:
    destination = Path(file_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(payload.model_dump_json(), encoding="utf-8")
    temporary.replace(destination)


def write_gcs_segment(payload: SegmentUpload) -> None:
    storage_client = storage.Client()
    bucket = storage_client.bucket(required_env("SEGMENTS_BUCKET"))
    blob = bucket.blob(normalized_object_name(payload.timestamp))
    blob.upload_from_string(
        payload.model_dump_json(),
        content_type="application/json",
    )


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.post("/upload")
def upload_segment(
    payload: SegmentUpload,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
) -> dict[str, bool]:
    expected_api_key = required_env("INGEST_API_KEY")
    if not x_api_key or x_api_key != expected_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    local_file = segments_file_path()
    if local_file:
        write_latest_local_segment(payload, local_file)
    else:
        write_gcs_segment(payload)

    return {"received": True}
