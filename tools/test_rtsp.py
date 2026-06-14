from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.password:
        return url

    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    user = parsed.username or ""
    auth = f"{user}:***@" if user else ""
    return parsed._replace(netloc=f"{auth}{host}").geturl()


def tcp_check(url: str, timeout: float) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 554
    if not host:
        raise ValueError("RTSP URL is missing a host")

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        print(f"TCP check failed for {host}:{port}: {exc}", file=sys.stderr)
        return False


def capture_frame(
    ffmpeg_bin: str,
    rtsp_url: str,
    output: Path,
    timeout: float,
) -> None:
    command = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-frames:v",
        "1",
        "-an",
        "-y",
        str(output),
    ]

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"FFmpeg binary not found: {ffmpeg_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out after {timeout}s while reading the RTSP stream"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFmpeg failed without stderr")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg exited successfully but did not create an image")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture one JPEG from an RTSP URL.")
    parser.add_argument(
        "rtsp_url",
        nargs="?",
        default=os.getenv("RTSP_URL"),
        help="RTSP URL. Defaults to RTSP_URL environment variable.",
    )
    parser.add_argument(
        "--output",
        default="rtsp_test_frame.jpg",
        help="Output image path.",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default=os.getenv("FFMPEG_BIN", "ffmpeg"),
        help="FFmpeg binary path.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Timeout in seconds.",
    )
    parser.add_argument(
        "--skip-tcp-check",
        action="store_true",
        help="Skip the preflight TCP connection check.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.rtsp_url:
        print("Provide an RTSP URL argument or set RTSP_URL.", file=sys.stderr)
        return 2

    output = Path(args.output).resolve()
    print(f"Testing {redact_url(args.rtsp_url)}")

    if not args.skip_tcp_check and not tcp_check(args.rtsp_url, timeout=5):
        return 1

    try:
        capture_frame(args.ffmpeg_bin, args.rtsp_url, output, args.timeout)
    except RuntimeError as exc:
        print(f"RTSP test failed: {exc}", file=sys.stderr)
        return 1

    print(f"OK: wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
