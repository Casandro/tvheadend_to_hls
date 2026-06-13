# Building and uploading the Docker image

Workflow for producing a new image and shipping it to a registry. For the
NAS-side configuration once the image is live, see `DEPLOYMENT.md`.

## Build locally

From the project root:

```bash
docker compose build
```

This reads `Dockerfile`, copies `src/tvhtohls/`, `list_channels.py`,
`entrypoint.sh`, and `nginx.conf` into a `debian:bookworm` base image and
tags the result as `tvheadend_to_hls-hls:latest`. Subsequent builds reuse
cached layers; the apt-install layer changes only when the package list in
`Dockerfile` changes.

A plain `docker build .` works too if you prefer not to use compose:

```bash
docker build -t tvhtohls:latest .
```

Run it briefly to sanity-check that the new image actually starts:

```bash
docker compose up -d --build
# Wait ~10 s for uvicorn to come up, then:
curl -fsS http://localhost/  | head -c 200    # should print HTML
docker compose down
```

(Replace port 80 with whatever you mapped if you've changed `compose.yaml`.)

## Push to a registry

Recommendation: **GitHub Container Registry (`ghcr.io`)** — free for public
images, no separate signup if the source already lives on GitHub.

### One-time setup

1. Create a Personal Access Token (PAT) at *GitHub → Settings → Developer
   settings → Personal access tokens → Tokens (classic)*. Scopes needed:
   `write:packages` and `read:packages`.
2. Log Docker into GHCR (uses the PAT as the password):

   ```bash
   echo "<paste-PAT-here>" | docker login ghcr.io -u <github-username> --password-stdin
   ```

   The credentials are saved under `~/.docker/config.json` for next time.

### Per-release push

```bash
# 1. Build (skipped if cache is current).
docker compose build

# 2. Tag the local image under your GHCR namespace.
docker tag tvheadend_to_hls-hls ghcr.io/<github-username>/tvhtohls:latest
# Optionally also pin to a version:
docker tag tvheadend_to_hls-hls ghcr.io/<github-username>/tvhtohls:v0.2.0

# 3. Push.
docker push ghcr.io/<github-username>/tvhtohls:latest
docker push ghcr.io/<github-username>/tvhtohls:v0.2.0   # if you tagged a version
```

By default GHCR creates the package as **private** on first push. To make it
pullable without credentials:

1. Go to `https://github.com/users/<github-username>/packages/container/tvhtohls/settings`.
2. *Change package visibility* → Public.

Repeated pushes to the same tag (e.g. `latest`) update the image. Any host
that pulls `latest` after that will get the new build.

## Versioning

Two conventions work well together:

- `latest` — moves with every push; convenient for "give me the current build"
  pulls (your dev TrueNAS).
- `vX.Y.Z` (immutable) — never overwritten once pushed. Pin production
  installs to one of these so an accidental push doesn't roll you forward.

The project doesn't have a real release cadence — bump the version anywhere
you like (e.g. in `pyproject.toml`'s `version`) and use the same value as the
tag.

## Multi-architecture builds

Plain `docker push` ships only the host's architecture (almost certainly
`linux/amd64`). For ARM hosts you'll need a multi-arch build:

```bash
# One-time: create a buildx builder that supports multi-platform.
docker buildx create --use --name multiarch

# Build + tag + push in one shot. Don't run `docker compose build` first;
# buildx replaces it for this command.
docker buildx build --platform linux/amd64,linux/arm64 \
    -t ghcr.io/<github-username>/tvhtohls:latest \
    -t ghcr.io/<github-username>/tvhtohls:v0.2.0 \
    --push .
```

The result is a manifest list — `docker pull` on either arch fetches the
right blob automatically.

## Verifying the push

Confirm the image landed and is pullable:

```bash
docker logout ghcr.io   # so the pull is unauthenticated (if public)
docker pull ghcr.io/<github-username>/tvhtohls:latest
docker inspect ghcr.io/<github-username>/tvhtohls:latest | grep -i created
```

`docker images ghcr.io/<github-username>/tvhtohls` lists what's available.

## Cleaning up local images

After a few rebuilds you'll accumulate dangling layers. Reclaim disk with:

```bash
docker image prune          # untagged dangling layers
docker image prune -a       # also untagged images (more aggressive)
```

## Quick reference

| Action | Command |
|---|---|
| Build | `docker compose build` |
| Smoke-test | `docker compose up -d --build && curl localhost/ \| head` |
| Login to GHCR | `docker login ghcr.io -u <user>` (paste PAT) |
| Tag for GHCR | `docker tag tvheadend_to_hls-hls ghcr.io/<user>/tvhtohls:latest` |
| Push | `docker push ghcr.io/<user>/tvhtohls:latest` |
| Multi-arch one-shot | `docker buildx build --platform linux/amd64,linux/arm64 -t … --push .` |
