# Runbook: Restarting the Service

Most of the time you do not need to restart anything — upgrades are handled
by `docker compose up -d` (see `runbooks/upgrade.md`).  Use this runbook
when the service is unresponsive, a container has exited unexpectedly, or you
need to apply a configuration change.

---

## Check current status first

```bash
# Are both containers running?
docker compose -f /opt/repo-scanner/docker-compose.yml ps

# Any recent errors?
docker compose -f /opt/repo-scanner/docker-compose.yml logs --tail=50

# Systemd service status
sudo systemctl status repo-scanner
```

---

## Restart the scanner container only

Use this when the app is misbehaving but Nginx is fine.  Nginx stays up and
will return 502 for the few seconds the app container is restarting.

```bash
cd /opt/repo-scanner
docker compose restart scanner
```

Verify:

```bash
docker compose ps
curl https://scanner.corp.example.com/api/v1/health
```

---

## Restart the full stack

Use this after a configuration change to `docker-compose.yml` or `nginx.conf`,
or when both containers need to be cycled.

```bash
cd /opt/repo-scanner
docker compose down
docker compose up -d
```

Brief downtime (~5 seconds) while Nginx restarts.

---

## Restart via systemd

Equivalent to the full stack restart above, but goes through systemd so
restart events are recorded in the journal.

```bash
sudo systemctl restart repo-scanner
```

Check the journal:

```bash
journalctl -u repo-scanner -n 50
```

---

## Force-kill a stuck container

If a container is not responding to `docker compose down`:

```bash
docker kill repo-scanner-scanner-1   # app container
docker kill repo-scanner-nginx-1     # nginx container
docker compose up -d                 # bring back up
```

Container names can be confirmed with `docker ps`.

---

## After any restart — confirm healthy

```bash
# Both containers running
docker compose -f /opt/repo-scanner/docker-compose.yml ps

# Health endpoint responds
curl https://scanner.corp.example.com/api/v1/health

# No errors in recent logs
docker compose -f /opt/repo-scanner/docker-compose.yml logs --tail=30
```
