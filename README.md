# Orange Pi Surveillance System

This monorepo contains the Orange Pi camera client, three FastAPI Cloud Run
services, a web viewer, mTLS certificate tooling, nginx mTLS termination config,
and deployment scripts.

## Repository Layout

```text
orangepi/
cloud_apis/ingest/
cloud_apis/status/
cloud_apis/viewer/
certs/
```

## Encryption Contract

The Orange Pi client encrypts each JPEG frame before upload with AES-256-CBC
from `pycryptodome`.

- `ENCRYPT_KEY` must be a 32-byte hex string.
- Each encrypted frame is base64 of `IV + ciphertext`.
- The IV is 16 random bytes.
- Plaintext JPEG bytes are padded with PKCS7 before encryption.
- The viewer reverses the same format and returns base64 JPEGs to the frontend.

Generate a key:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
```

## Certificates And mTLS

Generate a private CA, server certificate, and client `.p12`:

```bash
cd certs
P12_PASSWORD='choose-a-strong-export-password' \
SERVER_DNS='viewer.example.com' \
bash ./generate_certs.sh
```

Outputs are written to `certs/generated/`:

- `ca.crt` and `ca.key`: private CA material.
- `server.crt` and `server.key`: for nginx TLS termination.
- `client.p12`: install this on the viewing phone/laptop/browser profile.

Install `client.p12` on the viewer device and trust/select it when the browser
prompts for a client certificate. Keep `ca.key`, `server.key`, and the p12
password private.

For Google Cloud Run, the recommended production mTLS shape is:

1. Deploy `cloud_apis/viewer` with `--ingress internal-and-cloud-load-balancing`.
2. Put an External HTTPS Load Balancer in front of the Cloud Run backend.
3. Configure the load balancer mTLS trust store with `ca.crt`.
4. Route only verified clients to the viewer service.

The repo also includes `cloud_apis/viewer/nginx/nginx.conf` and an optional
container entrypoint mode for standalone or sidecar TLS termination. Mount
`ca.crt`, `server.crt`, and `server.key` at `/etc/nginx/certs/` and run the
viewer image with `USE_NGINX_MTLS=true`.

## Google Cloud Setup

Enable APIs:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com storage.googleapis.com firestore.googleapis.com
```

Create a bucket and Firestore database:

```bash
gcloud storage buckets create gs://YOUR_SEGMENTS_BUCKET --location=europe-west1
gcloud firestore databases create --location=europe-west1
```

Deploy each service from its own directory.

### Ingest API

Environment variables:

- `SEGMENTS_BUCKET`
- `INGEST_API_KEY`

Deploy:

```bash
cd cloud_apis/ingest
PROJECT_ID='your-project' \
REGION='europe-west1' \
SEGMENTS_BUCKET='YOUR_SEGMENTS_BUCKET' \
INGEST_API_KEY='shared-ingest-secret' \
bash ./deploy.sh
```

Endpoint:

- `POST /upload`
- Header: `X-Api-Key: <INGEST_API_KEY>`

The service account needs permission to write objects to `SEGMENTS_BUCKET`.

### Status API

Deploy:

```bash
cd cloud_apis/status
PROJECT_ID='your-project' \
REGION='europe-west1' \
bash ./deploy.sh
```

Endpoints:

- `GET /status`
- `POST /status/viewing`

The service account needs Firestore read/write access.

### Viewer

Environment variables:

- `SEGMENTS_BUCKET`
- `ENCRYPT_KEY`
- `STATUS_SERVICE_URL`

Deploy:

```bash
cd cloud_apis/viewer
PROJECT_ID='your-project' \
REGION='europe-west1' \
SEGMENTS_BUCKET='YOUR_SEGMENTS_BUCKET' \
ENCRYPT_KEY='same-32-byte-hex-key-as-orangepi' \
STATUS_SERVICE_URL='https://status-service-url' \
bash ./deploy.sh
```

The service account needs read access to `SEGMENTS_BUCKET`. Configure mTLS at
the HTTPS Load Balancer layer, or use the included nginx config in an environment
where the container can terminate TLS directly.

## Single VM Latest-Only Storage

When `ingest` and `viewer` run on the same VM, you can avoid Cloud Storage for
camera segments. Set `SEGMENTS_FILE` in both containers and mount the same Docker
volume at `/data`.

In this mode:

- `ingest` writes every upload to the same file, for example `/data/latest.json`.
- The next 5-second segment atomically replaces the previous one.
- `viewer` reads only that latest file.
- No historical segment objects are created.
- `SEGMENTS_BUCKET` is not required for `ingest` or `viewer`.

Create a shared volume:

```bash
docker volume create surveillance-segments
```

Run ingest:

```bash
docker run -d --restart unless-stopped \
  --name surveillance-ingest \
  --network surveillance \
  -p 8081:8080 \
  -e INGEST_API_KEY='shared-ingest-secret' \
  -e SEGMENTS_FILE=/data/latest.json \
  -v surveillance-segments:/data \
  surveillance-ingest
```

Run viewer:

```bash
docker run -d --restart unless-stopped \
  --name surveillance-viewer \
  --network surveillance \
  -p 443:8443 \
  -e PORT=8443 \
  -e USE_NGINX_MTLS=true \
  -e ENCRYPT_KEY='same-32-byte-hex-key-as-orangepi' \
  -e STATUS_SERVICE_URL='http://surveillance-status:8080' \
  -e SEGMENTS_FILE=/data/latest.json \
  -v surveillance-segments:/data:ro \
  -v /etc/surveillance/certs:/etc/nginx/certs:ro \
  surveillance-viewer
```

## Orange Pi Client

Install Python dependencies:

```bash
cd orangepi
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `.env`:

```dotenv
CAMERA_IP=192.168.1.50
CAMERA_USER=your-camera-account-user
CAMERA_PASSWORD=your-camera-account-password
ENCRYPT_KEY=same-32-byte-hex-key-as-viewer
INGEST_API_URL=https://ingest-service-url/upload
STATUS_API_URL=https://status-service-url/status
INGEST_API_KEY=shared-ingest-secret
FRAME_INTERVAL_SECONDS=1
```

Run:

```bash
python3 main.py
```

The client polls `STATUS_API_URL` every 30 seconds while idle. When the status
is `visualizando`, it captures RTSP frames from the Tapo camera, encrypts each
JPEG, batches frames into 5-second segments, and uploads to the ingest API.
During active viewing it checks status once per second and stops uploading when
the status returns to `idle`.

## Payload Shape

Orange Pi upload payload:

```json
{
  "timestamp": "2026-06-13T01:23:45Z",
  "segment_duration_seconds": 5,
  "frames": ["<base64_encrypted_jpeg>"]
}
```

Viewer response:

```json
{
  "segments": [
    {
      "timestamp": "2026-06-13T01:23:45Z",
      "frames": ["<base64_jpeg>"]
    }
  ]
}
```
