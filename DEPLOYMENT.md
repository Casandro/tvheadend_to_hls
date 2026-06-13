# Deployment

Notes for running the container somewhere other than the build host —
especially on a NAS like TrueNAS SCALE that pulls images from a registry.

## Custom host port

`compose.yaml` defaults to publishing on host port 80. If that port is taken
(common on a NAS already running a web UI), change the left side of the port
mapping:

```yaml
services:
  hls:
    # ...
    ports:
      - "8888:80"   # host_port:container_port — pick any free host port
```

After that, open `http://<host>:8888/` instead of `http://<host>/`. The change
is purely in the port mapping; nginx inside the container still listens on 80.

On TrueNAS SCALE's *Custom App* UI the equivalent is the **External port**
field (set to 8888 or whatever's free); **Internal port** stays 80.

## Pushing the image to a registry

For a NAS to pull the image it has to live in a registry the NAS can reach.
GitHub Container Registry (`ghcr.io`) is free for public images and is the
simplest path if the source already lives on GitHub.

### One-time setup

1. Create a GitHub Personal Access Token (PAT) with `write:packages` and
   `read:packages` scope (Settings → Developer settings → Personal access
   tokens → Tokens (classic)).
2. Log Docker into GHCR:

   ```bash
   echo "<your-PAT>" | docker login ghcr.io -u <your-github-username> --password-stdin
   ```

### Each release

```bash
# Build (skips if cached)
docker compose build

# Tag the image under your namespace
docker tag tvheadend_to_hls-hls ghcr.io/<user>/tvhtohls:latest

# Push
docker push ghcr.io/<user>/tvhtohls:latest
```

The image becomes pullable at `ghcr.io/<user>/tvhtohls:latest`. By default
ghcr.io creates the package as private — visit `https://github.com/users/<user>/packages/container/tvhtohls/settings`
and change visibility to public if you want unauthenticated pulls.

### Cross-architecture (only if your NAS isn't amd64)

Most TrueNAS SCALE hosts are amd64, in which case a plain `docker push` works.
For ARM hosts (some smaller appliances), use buildx:

```bash
docker buildx create --use --name multi   # one-time
docker buildx build --platform linux/amd64,linux/arm64 \
    -t ghcr.io/<user>/tvhtohls:latest --push .
```

## TrueNAS SCALE — Custom App

I haven't tested this against an actual TrueNAS instance, so treat the field
names as approximate; the underlying knobs are standard.

Apps → Discover Apps → **Custom App** (or *Install via YAML* on newer
versions). Set:

| Field | Value |
|-------|-------|
| Application Name | `tvhtohls` |
| Image Repository | `ghcr.io/<user>/tvhtohls` |
| Image Tag | `latest` (or a specific version) |
| Pull Policy | `IfNotPresent` for stable, `Always` if you push `latest` often |
| External port | 8888 (or any free port) |
| Internal port | 80 |
| Restart policy | Always |

Environment variables — same set as a local `.env` file:

| Name | Value |
|------|-------|
| `tvheadend_user` | your TVHeadend username |
| `tvheadend_pass` | your TVHeadend password |
| `tvheadend_ip` | TVHeadend host IP |
| `top_channels` (optional) | comma-separated UUIDs |
| `top_channels_<name>` (optional) | extra named pin lists |
| `hwaccel` (optional) | `auto` (default), `vaapi`, or `none` |

Host devices to pass through (only needed for VAAPI acceleration; safe to omit
for CPU-only):

- `/dev/dri` → `/dev/dri` (read/write)

Storage: the container writes HLS segments to `/tmp/tvhtohls/hls` inside the
container. A small in-memory volume is enough — these files turn over every
few seconds. If TrueNAS's Custom App UI requires a host path, pick a
short-lived directory like `/mnt/tank/apps/tvhtohls/hls` (or use a `tmpfs`
mount in the YAML).

Container's HTTP service runs on port 80, so once the External port is
mapped, open `http://<truenas>:<external_port>/` in a browser.

## Updating

When you push a new image tag to GHCR:

1. From the host: `docker pull ghcr.io/<user>/tvhtohls:latest`
2. From TrueNAS: hit the app's "Roll back" / "Edit" → "Save" or use the
   "Check for updates" button.

For the local `docker compose` workflow, `docker compose pull && docker compose up -d`
restarts with the new image.
