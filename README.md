# dark-monitoring

Synthetic availability monitoring for the dARK platform with Prometheus,
Blackbox Exporter and Grafana. It is a separate deployment unit, but is kept
next to `dark-deployer` so its target inventory is generated from the same
`.env`, `.env.integration` and `storage-topology.json` that configure dARK.

It probes the externally reachable API of each service; it does **not** scrape
application internals. This is the right first signal for an unavailable
service, an unreachable VPN path or a failed Store API readiness dependency.

## Covered targets

| Service | Probe |
| --- | --- |
| Admin API | `GET /health` |
| Minter API | `GET /health` |
| Resolver API | `GET /health` |
| Store API | `GET /health/live`, `/health/read`, `/health/write` |
| Dashboard | `GET /` |
| Blockchain | JSON-RPC `eth_chainId` request |
| Each Kubo peer | `POST /api/v0/version` |
| Each Cluster peer | `GET /id` |

The target generator uses `PRODUCTION_*_PUBLIC_URL` when available. In the
developer profile it uses the local dARK ports. A local `localhost` URL is
translated to `host.docker.internal` because the prober runs in Docker. For a
remote monitor, provide public/VPN-reachable URLs in the deployer `.env` or
pass alternate files to the generator.

## Start locally

```bash
cd dark-monitoring
cp .env.example .env
# Set a strong, unique GRAFANA_ADMIN_PASSWORD in .env.
python3 scripts/generate_targets.py
docker compose up -d
```

Grafana is then available only on `http://127.0.0.1:3000`; Prometheus is only
on `http://127.0.0.1:9090`. The provisioned **dARK / Availability** dashboard
shows endpoint availability and latency. `generated/` is excluded from Git.

Run the generator after changing `.env`, `.env.integration`, topology or the
dashboard URL, then reload Prometheus without a container restart:

```bash
python3 scripts/generate_targets.py
curl -X POST http://127.0.0.1:9090/-/reload
```

For a dashboard environment file in another location:

```bash
python3 scripts/generate_targets.py \
  --dashboard-env /opt/dark/dashboard/.env \
  --env /opt/dark/.env \
  --integration-env /opt/dark/.env.integration \
  --topology /opt/dark/storage-topology.json
```

## Production notes

Run this on a dedicated monitoring host with VPN access to the apps,
blockchain and storage hosts. Do not publish Kubo/Cluster administrative ports
to the internet merely for monitoring; allow the monitoring host on the VPN
in the firewall policy. Put Grafana behind the VPN or an authenticated reverse
proxy, and replace the sample Grafana password before first startup.

The included rules flag a failed endpoint after two minutes, slow HTTP probes
after five minutes, and Store API write-readiness after one minute. They are
visible in Prometheus/Grafana. To notify people, add Alertmanager plus the
chosen receiver (email, Slack, PagerDuty, etc.); no notification channel is
configured here because that would require operational credentials.

For CPU, memory, disk, container restarts, IPFS pin counts and application
business metrics, add node/cAdvisor exporters and native `/metrics` endpoints
to the corresponding projects. Those metrics are complementary to—not a
replacement for—the external probes in this project.

## Docker DNS portability

Image downloads happen before Compose creates a network, so a Docker daemon
with an unreachable static DNS override can fail before this project starts.
Do not replace it with a fixed router or public DNS address: that breaks when a
server changes network and can bypass split DNS used by a VPN.

The optional helper removes only Docker's `dns` setting and preserves every
other setting in `/etc/docker/daemon.json`, after creating a timestamped
backup. Docker then follows the host's dynamic resolver on developer machines
and servers alike:

```bash
# Inspect only; makes no change.
sudo python3 scripts/configure_docker_dns.py

# Apply it, then restart Docker (this restarts running containers).
sudo python3 scripts/configure_docker_dns.py --apply
sudo systemctl restart docker

docker compose pull
docker compose up -d
```

Use this only when `daemon.json` has a static `dns` field that is known to be
unreachable. If the host itself cannot resolve Docker Hub, correct the host or
network DNS instead.
