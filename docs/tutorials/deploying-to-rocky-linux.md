# Deploying Repository Scanner to Rocky Linux

This tutorial walks through deploying the scanner on a Rocky Linux VM using
Docker and Nginx.  The result is a production-ready service accessible over
HTTPS, managed by systemd, surviving reboots automatically.

## Architecture

```
Callers (browsers, curl, CI agents)
              ↓  HTTPS :443
           Nginx container        ← TLS termination
              ↓  HTTP :8080 (internal Docker network)
         Scanner container        ← Gunicorn + Flask
```

Both containers are managed by Docker Compose.  Systemd starts the Compose
stack on boot and restarts it on failure.

---

## Prerequisites on the VM

Install Docker and the Compose plugin (Rocky Linux 8/9):

```bash
sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```

Verify:

```bash
docker --version
docker compose version
```

---

## Step 1 — Create the application directory

```bash
sudo mkdir -p /opt/repo-scanner/certs
sudo chown -R $USER:$USER /opt/repo-scanner
```

---

## Step 2 — Copy deploy files from the repo

From your workstation (or Jenkins), copy the deploy files to the VM:

```bash
scp deploy/docker-compose.yml  user@vm-hostname:/opt/repo-scanner/
scp deploy/nginx.conf          user@vm-hostname:/opt/repo-scanner/
```

---

## Step 3 — Generate TLS certificates

Run this **on your workstation** from the root of the repo, substituting your
VM's DNS hostname:

```bash
bash deploy/generate-certs.sh scanner.corp.example.com
```

This creates four files in `deploy/certs/`:

| File | Purpose |
|---|---|
| `ca.key` | CA private key — never leave your workstation |
| `ca.crt` | CA certificate — distribute to all callers |
| `server.key` | Server private key — copy to VM |
| `server.crt` | Server certificate — copy to VM |

Copy the server cert and key to the VM:

```bash
scp deploy/certs/server.crt user@vm-hostname:/opt/repo-scanner/certs/
scp deploy/certs/server.key user@vm-hostname:/opt/repo-scanner/certs/
```

> **Keep `ca.key` and `server.key` private.**  The CA key is only needed if
> you generate a new server cert in future.  The server key must never leave
> the VM.

---

## Step 4 — Distribute the CA certificate to callers

Every machine that calls the scanner needs to trust your CA.  Without this,
browsers and `curl` will reject the self-signed certificate.

### Rocky Linux / RHEL (CI agents, other servers)

```bash
sudo cp ca.crt /etc/pki/ca-trust/source/anchors/repo-scanner-ca.crt
sudo update-ca-trust
```

### macOS (developer workstations)

```bash
sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain ca.crt
```

### Windows

Double-click `ca.crt` → Install Certificate → Local Machine →
Trusted Root Certification Authorities.

### curl (without system trust)

```bash
curl --cacert ca.crt https://scanner.corp.example.com/api/v1/health
```

---

## Step 5 — Load the Docker image

Download the `.tar.gz` artifact from Jenkins
(`Build → Artifacts → repo-scanner-<N>.tar.gz`), then copy it to the VM:

```bash
scp repo-scanner-42.tar.gz user@vm-hostname:/opt/repo-scanner/
```

On the VM, load it into Docker:

```bash
cd /opt/repo-scanner
docker load -i repo-scanner-42.tar.gz
```

Verify the image is present:

```bash
docker images repo-scanner
```

---

## Step 6 — Start the stack

```bash
cd /opt/repo-scanner
docker compose up -d
```

Check both containers are running:

```bash
docker compose ps
```

Test HTTPS is working:

```bash
curl https://scanner.corp.example.com/api/v1/health
# Expected: {"data": {"status": "ok"}}
```

---

## Step 7 — Install the systemd service

This makes the stack start automatically on boot and restart on failure:

```bash
sudo cp /path/to/repo/deploy/scanner.service /etc/systemd/system/repo-scanner.service
sudo systemctl daemon-reload
sudo systemctl enable repo-scanner
```

The service uses `WorkingDirectory=/opt/repo-scanner` and runs
`docker compose up -d` on start, so everything must be in that directory.

Test the service controls work:

```bash
sudo systemctl stop  repo-scanner
sudo systemctl start repo-scanner
sudo systemctl status repo-scanner
```

---

## Viewing logs

```bash
# All containers
docker compose -f /opt/repo-scanner/docker-compose.yml logs -f

# App only
docker compose -f /opt/repo-scanner/docker-compose.yml logs -f scanner

# Via journald (systemd captured output)
journalctl -u repo-scanner -f
```

---

## Firewall

Open ports 80 and 443 on the VM if a firewall is in place:

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

Port 8080 (Gunicorn) does **not** need to be open — it is only accessible
inside the Docker network.

---

## Ongoing operations

For day-to-day operations after the initial deployment, refer to the runbooks:

- **Upgrading** — `docs/runbooks/upgrade.md`
- **Renewing TLS certificates** — `docs/runbooks/renew-certificates.md`
- **Restarting the service** — `docs/runbooks/restart-service.md`
- **Diagnosing scan failures** — `docs/runbooks/diagnose-scan-failure.md`
