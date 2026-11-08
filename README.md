# dark-monitoring

New platform developer: follow [developer onboarding](https://github.com/LA-Referencia-IOI/dark-deployer/blob/main/docs/developer-onboarding.md)
for the complete local stack, signed requests and component iteration.

Operational monitoring for the dARK platform with Prometheus, Blackbox
Exporter, node-exporter, cAdvisor and Grafana. It lives at
`components/dark-monitoring` inside `dark-deployer`.

The installer supports a legacy version-3 `deployment-topology.json` snapshot
and a separate environment-based target generator. The current deployer does not
provide a documented export of that legacy snapshot; do not assume an operator
inventory or current session has the same schema. Configure targets explicitly
and verify them against the applied deployment and network reachability.

It probes the externally reachable API of each service; it does **not** scrape
application internals. This is the right first signal for an unavailable
service, an unreachable VPN path or a failed Store API readiness dependency.

## Target types

The legacy snapshot generator can derive the following targets when the
snapshot contains a reachable endpoint. The environment-based generator builds
a different set: application HTTP targets, an RPC target when configured, and
Kubo/Cluster targets only when an appropriate legacy topology is supplied. It
does not automatically add Explorer targets. An explicit HTTP target list
replaces automatic HTTP discovery, including automatic Cluster HTTP targets.
Inspect the generated files to confirm actual coverage.

| Service | Probe |
| --- | --- |
| Admin API | `GET /health` |
| Minter API | `GET /health` |
| Resolver API | `GET /health` |
| Store API | `GET /health/live`, `/health/read`, `/health/write` |
| Dashboard | `GET /login` (internal container route) |
| Block Explorer | `GET /health`, JSON-RPC `eth_chainId` through `/jsonrpc` |
| Blockchain | JSON-RPC `eth_chainId` request |
| Each Kubo peer | `POST /api/v0/version` |
| Each Cluster peer | `GET /id` |

The target generator uses `PRODUCTION_*_PUBLIC_URL` when available. In the
developer profile it uses the local dARK ports. A local `localhost` URL is
translated to `host.docker.internal` because the prober runs in Docker. For a
remote monitor, provide public/VPN-reachable URLs in the deployer `.env` or
pass alternate files to the generator.

For a decoupled installation, create a target list and reference it from this
project's `.env`:

```ini
DARK_MONITOR_TARGETS_FILE=/opt/dark-monitoring/dark-monitoring-targets.json
```

Alternatively, configure the complete HTTP target list directly. A non-empty
list replaces automatic HTTP discovery:

```ini
DARK_MONITOR_TARGETS_JSON=[{"name":"admin","url":"https://admin.example/health","labels":{"site":"site-a"}}]
```

Set only one of `DARK_MONITOR_TARGETS_FILE` and a non-empty `DARK_MONITOR_TARGETS_JSON`.
Blockchain JSON-RPC and Kubo probes continue to use the deployer integration
environment and storage topology because they require specialized HTTP probe
methods.

## Start locally

```bash
cd components/dark-monitoring
python3 install.py
```

On its first run, the installer creates `.env` with restrictive permissions
and stops. Set a strong, unique `GRAFANA_ADMIN_PASSWORD` and configure target
URLs, then run it again. Without `--deployment-snapshot`, the installer uses the
environment-based generator, validates Compose, starts the stack and checks
Prometheus and Grafana readiness. Use addresses reachable from the monitoring
containers. The optional `--deployment-snapshot` route accepts the older version-3
snapshot schema and generates Docker network attachments from that snapshot; use
it only when a compatible, applied snapshot is available.

Grafana is then available only on `http://127.0.0.1:3000`; Prometheus is only
on `http://127.0.0.1:9090`. The provisioned **dARK / Availability** dashboard
shows endpoint availability and latency. This component's `.gitignore` excludes
only `generated/*.json` and `generated/*.prom` from Git; any other file written
under `generated/` is tracked.

Anonymous Grafana access is enabled with the read-only `Viewer` role so the
dARK Dashboard can open `/dashboards` without handling or exposing Grafana
credentials. Administrative actions still require the configured administrator
account. Set `GRAFANA_ANONYMOUS_ENABLED=false` when Grafana is published through
another authentication layer or when anonymous viewing is not acceptable.

To expose navigation entries for administrators in `dashboard-web`, set
`GRAFANA_URL` to the operator-facing Grafana `/dashboards` URL and
`PROMETHEUS_URL` to its `/targets` URL. The Dashboard only opens the links in a
new tab, and no monitoring credential is placed in Dashboard HTML.

For the environment-based configuration, regenerate after changing its input
files, then reload Prometheus without a container restart. This command writes
the same target files as the legacy snapshot generator; do not use it to refresh
a snapshot-based configuration:

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

node-exporter supplies host filesystem and inode data that Docker metrics do
not provide. cAdvisor supplies container resource series and preserves Compose
labels, including `org.dark.deployment.id` and `org.dark.service.id`, so Grafana
can use the same stable service identity as `deploy.py metrics`. Application
business metrics, IPFS pin counts and consensus-specific metrics still require
native `/metrics` endpoints in the corresponding projects.

The interactive `deploy.py metrics` collector remains the preferred ad-hoc
local/SSH diagnostic tool. Its parser is also reused by `deploy.py
metrics-export`, which atomically writes `generated/dark.prom` for
node-exporter's textfile collector. Prometheus does not run an SSH probe during
a scrape; schedule collection independently, for example once per minute:

```bash
cd /path/to/dark-deployer
venv/bin/python deploy.py metrics-export \
  --deployment dark-operator-local-ha \
  --include-disk \
  --output components/dark-monitoring/generated/dark.prom
```

Use a systemd timer or equivalent scheduler in production, prevent overlapping
runs, and alert on `time() - dark_metrics_export_timestamp_seconds`. Direct
exporters provide continuous samples while Blackbox validates reachability.
`dark_docker_disk_size_bytes`, `dark_docker_disk_reclaimable_bytes` and
`dark_docker_disk_objects` describe Docker objects reported by `docker system
df`; they are not filesystem, `/srv/dark/data`, root-volume or EBS capacity
metrics. Those remain node-exporter filesystem metrics.

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

python3 install.py
```

Use this only when `daemon.json` has a static `dns` field that is known to be
unreachable. If the host itself cannot resolve Docker Hub, correct the host or
network DNS instead.

## Operations

```bash
# Stop Blackbox, Prometheus and Grafana; retain containers and data volumes.
python3 stop.py

# Remove containers, network, volumes, images and generated targets.
python3 clean.py

# Optional: perform cleanup but keep downloaded images for a faster reinstall.
python3 clean.py --keep-images
```

`clean.py` preserves the repository source and `.env`. Its destructive action
requires interactive confirmation unless `--yes` is supplied for automation.

## Current operator-v3 local example

For the platform onboarding lab, run monitoring as a separate Compose project
and attach Blackbox to the application's Docker network. Start with explicit
targets instead of passing the operator inventory as a legacy snapshot.

Run `python3 install.py` once to create `.env`. Set its generated Grafana password
and the following variable in that file, then run the installer again:

```dotenv
DARK_MONITOR_TARGETS_JSON=[{"name":"minter","url":"http://minter-api:8001/health","labels":{"site":"local"}}]
```

After the installer starts the project, attach its prober to the local lab's
network. Use the network name discovered in the onboarding guide (for the
unmodified single-machine inventory, `dark-operator-local-local`):

```bash
# Run from this component's root; replace the network for a different inventory.
DARK_DEV_NETWORK=dark-operator-local-local
DARK_DEV_BLACKBOX=$(docker compose ps -q blackbox-exporter)
docker network connect "$DARK_DEV_NETWORK" "$DARK_DEV_BLACKBOX"
```

Check the target in Prometheus at `http://127.0.0.1:9090/targets` and the
Availability dashboard in Grafana at `http://127.0.0.1:3000`. The target cannot
resolve/reach Minter until the network attachment exists. Repeat the attachment
when Blackbox is recreated. After editing targets, regenerate them with
`python3 scripts/generate_targets.py` and reload Prometheus as above.

Add other API/Store HTTP targets explicitly as needed. This list replaces HTTP
auto-discovery and does not imply that RPC, Kubo or consensus are monitored.
Dashboard navigation links also need explicit URL configuration; they do not
install monitoring. Host-local gateway targets are a different path: a listener
bound only to loopback may not be reachable through `host.docker.internal` on
Linux, so prefer the shared-network route for this local lab.
