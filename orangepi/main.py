from __future__ import annotations

import base64
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import cv2
import requests
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
from dotenv import load_dotenv
from pytapo import Tapo


STATUS_IDLE = "idle"
STATUS_VIEWING = "visualizando"
STATUS_POLL_SECONDS = 30
ACTIVE_STATUS_CHECK_SECONDS = 1
SEGMENT_DURATION_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 10
MAX_UPLOAD_RETRIES = 3


@dataclass(frozen=True)
class Config:
    camera_ip: str
    camera_user: str
    camera_password: str
    tapo_api_user: str
    tapo_api_password: str
    rtsp_user: str
    rtsp_password: str
    encrypt_key: bytes
    ingest_api_url: str
    status_api_url: str
    ingest_api_key: str
    frame_interval_seconds: float


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    load_dotenv()

    key_hex = required_env("ENCRYPT_KEY")
    try:
        encrypt_key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise RuntimeError("ENCRYPT_KEY must be a 32-byte hex string") from exc
    if len(encrypt_key) != 32:
        raise RuntimeError("ENCRYPT_KEY must decode to exactly 32 bytes")

    return Config(
        camera_ip=required_env("CAMERA_IP"),
        camera_user=required_env("CAMERA_USER"),
        camera_password=required_env("CAMERA_PASSWORD"),
        tapo_api_user=os.getenv("TAPO_API_USER", required_env("CAMERA_USER")),
        tapo_api_password=os.getenv(
            "TAPO_API_PASSWORD",
            required_env("CAMERA_PASSWORD"),
        ),
        rtsp_user=os.getenv("RTSP_USER", required_env("CAMERA_USER")),
        rtsp_password=os.getenv("RTSP_PASSWORD", required_env("CAMERA_PASSWORD")),
        encrypt_key=encrypt_key,
        ingest_api_url=required_env("INGEST_API_URL"),
        status_api_url=required_env("STATUS_API_URL"),
        ingest_api_key=required_env("INGEST_API_KEY"),
        frame_interval_seconds=float(os.getenv("FRAME_INTERVAL_SECONDS", "1")),
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encrypt_jpeg(jpeg_bytes: bytes, key: bytes) -> str:
    iv = get_random_bytes(AES.block_size)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(jpeg_bytes, AES.block_size))
    return base64.b64encode(iv + ciphertext).decode("ascii")


class TapoFrameSource:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.capture: cv2.VideoCapture | None = None

        logging.info("Connecting to Tapo camera at %s", config.camera_ip)
        self.tapo = Tapo(
            config.camera_ip,
            config.tapo_api_user,
            config.tapo_api_password,
        )
        logging.info("Tapo camera session established")
        self.open()

    @property
    def rtsp_url(self) -> str:
        user = quote(self.config.rtsp_user, safe="")
        password = quote(self.config.rtsp_password, safe="")
        return f"rtsp://{user}:{password}@{self.config.camera_ip}:554/stream1"

    def open(self) -> None:
        self.close()
        logging.info("Opening RTSP stream")
        self.capture = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if not self.capture.isOpened():
            self.capture.release()
            self.capture = None
            raise RuntimeError("Could not open camera RTSP stream")

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def capture_jpeg(self) -> bytes:
        if self.capture is None or not self.capture.isOpened():
            self.open()

        assert self.capture is not None
        ok, frame = self.capture.read()
        if not ok or frame is None:
            logging.warning("Frame read failed; reopening RTSP stream")
            self.open()
            assert self.capture is not None
            ok, frame = self.capture.read()
            if not ok or frame is None:
                raise RuntimeError("Could not read frame from camera")

        encoded_ok, encoded = cv2.imencode(".jpg", frame)
        if not encoded_ok:
            raise RuntimeError("Could not encode camera frame as JPEG")
        return encoded.tobytes()


class SurveillanceClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.http = requests.Session()
        self.stop_requested = False

    def request_stop(self, *_: Any) -> None:
        self.stop_requested = True

    def fetch_status(self) -> str:
        response = self.http.get(
            self.config.status_api_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        status = response.json().get("status")
        if status not in {STATUS_VIEWING, STATUS_IDLE}:
            raise RuntimeError(f"Unexpected status API response: {status!r}")
        return str(status)

    def wait_while_idle(self) -> bool:
        for _ in range(STATUS_POLL_SECONDS):
            if self.stop_requested:
                return False
            time.sleep(1)
        return True

    def collect_segment(self, source: TapoFrameSource) -> dict[str, Any] | None:
        started_at = utc_now_iso()
        frames: list[str] = []
        deadline = time.monotonic() + SEGMENT_DURATION_SECONDS
        next_status_check = 0.0

        while time.monotonic() < deadline and not self.stop_requested:
            if time.monotonic() >= next_status_check:
                status = self.fetch_status()
                if status != STATUS_VIEWING:
                    logging.info("Viewer status is idle; pausing uploads")
                    return None
                next_status_check = time.monotonic() + ACTIVE_STATUS_CHECK_SECONDS

            jpeg = source.capture_jpeg()
            frames.append(encrypt_jpeg(jpeg, self.config.encrypt_key))
            time.sleep(max(self.config.frame_interval_seconds, 0.1))

        if not frames:
            return None

        return {
            "timestamp": started_at,
            "segment_duration_seconds": SEGMENT_DURATION_SECONDS,
            "frames": frames,
        }

    def upload_segment(self, payload: dict[str, Any]) -> None:
        headers = {"X-Api-Key": self.config.ingest_api_key}
        for attempt in range(MAX_UPLOAD_RETRIES + 1):
            try:
                response = self.http.post(
                    self.config.ingest_api_url,
                    json=payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                logging.info(
                    "Uploaded segment %s with %d frame(s)",
                    payload["timestamp"],
                    len(payload["frames"]),
                )
                return
            except requests.RequestException as exc:
                if attempt >= MAX_UPLOAD_RETRIES:
                    raise
                delay = min(2**attempt, 8)
                logging.warning(
                    "Upload attempt failed: %s; retry %d/%d in %ss",
                    exc,
                    attempt + 1,
                    MAX_UPLOAD_RETRIES,
                    delay,
                )
                time.sleep(delay)

    def run(self) -> None:
        source = TapoFrameSource(self.config)
        try:
            while not self.stop_requested:
                try:
                    status = self.fetch_status()
                except requests.RequestException as exc:
                    logging.warning("Status poll failed: %s", exc)
                    self.wait_while_idle()
                    continue

                if status != STATUS_VIEWING:
                    logging.info("Status is idle")
                    if not self.wait_while_idle():
                        break
                    continue

                logging.info("Status is visualizando; starting uploads")
                while not self.stop_requested:
                    try:
                        payload = self.collect_segment(source)
                        if payload is None:
                            break
                        self.upload_segment(payload)
                    except requests.RequestException as exc:
                        logging.warning("Network error during active upload loop: %s", exc)
                        time.sleep(1)
                    except Exception:
                        logging.exception("Camera or encoding error during active upload loop")
                        time.sleep(2)
        finally:
            source.close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    client = SurveillanceClient(load_config())
    signal.signal(signal.SIGINT, client.request_stop)
    signal.signal(signal.SIGTERM, client.request_stop)
    client.run()


if __name__ == "__main__":
    main()
