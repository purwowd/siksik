#!/usr/bin/env bash
# Generate self-signed TLS cert for SATRIA lab reverse proxy (PoC only).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_DIR="${1:-$DIR/certs}"
mkdir -p "$CERT_DIR"

CN="${SATRIA_TLS_CN:-satria.lab.local}"
DAYS="${SATRIA_TLS_DAYS:-825}"

openssl req -x509 -nodes -newkey rsa:4096 \
  -keyout "$CERT_DIR/privkey.pem" \
  -out "$CERT_DIR/fullchain.pem" \
  -days "$DAYS" \
  -subj "/CN=$CN/O=SATRIA Lab/C=ID"

echo "cert_ok dir=$CERT_DIR cn=$CN"
