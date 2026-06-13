from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI
from google.cloud import firestore
from pydantic import BaseModel


StatusValue = Literal["visualizando", "idle"]

app = FastAPI(title="Surveillance Status API")
firestore_client = firestore.Client()


class StatusResponse(BaseModel):
    status: StatusValue


def viewer_state_doc() -> firestore.DocumentReference:
    return firestore_client.collection("config").document("viewer_state")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    snapshot = viewer_state_doc().get()
    if not snapshot.exists:
        return StatusResponse(status="idle")

    data = snapshot.to_dict() or {}
    status = data.get("status", "idle")
    if status not in {"visualizando", "idle"}:
        status = "idle"
    return StatusResponse(status=status)


@app.post("/status/viewing")
def set_viewing() -> dict[str, bool]:
    viewer_state_doc().set(
        {
            "status": "visualizando",
            "updated_at": utc_now_iso(),
        }
    )
    return {"updated": True}
