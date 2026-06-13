#!/usr/bin/env bash
set -euo pipefail

: "${P12_PASSWORD:?Set P12_PASSWORD for the exported client certificate}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-${SCRIPT_DIR}/generated}"
SERVER_DNS="${SERVER_DNS:-viewer.local}"
SERVER_IP="${SERVER_IP:-127.0.0.1}"
CLIENT_NAME="${CLIENT_NAME:-viewer-client}"
DAYS="${DAYS:-825}"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes \
  -key ca.key \
  -sha256 \
  -days "$DAYS" \
  -out ca.crt \
  -subj "/CN=Surveillance Private CA"

openssl genrsa -out server.key 2048
cat > server.ext <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names

[alt_names]
DNS.1=${SERVER_DNS}
IP.1=${SERVER_IP}
EOF
openssl req -new \
  -key server.key \
  -out server.csr \
  -subj "/CN=${SERVER_DNS}"
openssl x509 -req \
  -in server.csr \
  -CA ca.crt \
  -CAkey ca.key \
  -CAcreateserial \
  -out server.crt \
  -days "$DAYS" \
  -sha256 \
  -extfile server.ext

openssl genrsa -out client.key 2048
cat > client.ext <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth
EOF
openssl req -new \
  -key client.key \
  -out client.csr \
  -subj "/CN=${CLIENT_NAME}"
openssl x509 -req \
  -in client.csr \
  -CA ca.crt \
  -CAkey ca.key \
  -CAcreateserial \
  -out client.crt \
  -days "$DAYS" \
  -sha256 \
  -extfile client.ext
openssl pkcs12 -export \
  -out client.p12 \
  -inkey client.key \
  -in client.crt \
  -certfile ca.crt \
  -passout "pass:${P12_PASSWORD}" \
  -name "$CLIENT_NAME"

rm -f server.csr client.csr server.ext client.ext

cat <<EOF
Generated certificates in: ${OUT_DIR}
- ca.crt: trust root for mTLS verification
- server.crt/server.key: TLS certificate and key for nginx
- client.p12: install this on the viewer device
EOF
