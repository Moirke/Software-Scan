#!/usr/bin/env bash
# generate-certs.sh — create a self-signed CA and server certificate
#
# Usage:
#   bash deploy/generate-certs.sh <hostname>
#
# Example:
#   bash deploy/generate-certs.sh scanner.corp.example.com
#
# Output (written to deploy/certs/):
#   ca.key       — CA private key      (keep private, never distribute)
#   ca.crt       — CA certificate      (distribute to all callers/browsers)
#   server.key   — server private key  (stays on the VM)
#   server.crt   — server certificate  (stays on the VM)
#
# The CA cert (ca.crt) must be installed as a trusted root on every machine
# that will call the scanner — browsers, curl clients, CI agents, etc.
# See the tutorial for instructions on distributing it.

set -euo pipefail

HOSTNAME="${1:?Usage: $0 <hostname>}"
OUT_DIR="$(dirname "$0")/certs"

mkdir -p "$OUT_DIR"

echo "Generating certs for: $HOSTNAME"
echo "Output directory:     $OUT_DIR"
echo ""

# ── 1. Certificate Authority ───────────────────────────────────────────────

echo "[1/4] Generating CA private key..."
openssl genrsa -out "$OUT_DIR/ca.key" 4096

echo "[2/4] Generating self-signed CA certificate (10-year validity)..."
openssl req -new -x509 \
    -days 3650 \
    -key "$OUT_DIR/ca.key" \
    -out "$OUT_DIR/ca.crt" \
    -subj "/CN=Repo Scanner CA/O=Internal/C=US"

# ── 2. Server certificate ──────────────────────────────────────────────────

echo "[3/4] Generating server private key..."
openssl genrsa -out "$OUT_DIR/server.key" 2048

echo "[4/4] Generating server certificate signed by CA (1-year validity)..."

# SAN (Subject Alternative Name) is required by modern browsers and curl.
# We include both the hostname and localhost so health checks work locally.
openssl req -new \
    -key "$OUT_DIR/server.key" \
    -out "$OUT_DIR/server.csr" \
    -subj "/CN=${HOSTNAME}/O=Internal/C=US"

openssl x509 -req \
    -days 365 \
    -in  "$OUT_DIR/server.csr" \
    -CA  "$OUT_DIR/ca.crt" \
    -CAkey "$OUT_DIR/ca.key" \
    -CAcreateserial \
    -out "$OUT_DIR/server.crt" \
    -extensions v3_req \
    -extfile <(cat <<EOF
[v3_req]
subjectAltName = DNS:${HOSTNAME},DNS:localhost,IP:127.0.0.1
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF
)

# Clean up CSR — not needed after signing
rm -f "$OUT_DIR/server.csr" "$OUT_DIR/ca.srl"

# ── Summary ───────────────────────────────────────────────────────────────

chmod 600 "$OUT_DIR/ca.key" "$OUT_DIR/server.key"
chmod 644 "$OUT_DIR/ca.crt" "$OUT_DIR/server.crt"

echo ""
echo "Done. Files written to $OUT_DIR:"
ls -lh "$OUT_DIR"
echo ""
echo "Next steps:"
echo "  1. Distribute $OUT_DIR/ca.crt to all callers (browsers, CI agents, curl clients)"
echo "  2. Start the stack:  docker compose -f deploy/docker-compose.yml up -d"
echo ""
echo "See tutorials/deploying-to-rocky-linux.md for full instructions."
