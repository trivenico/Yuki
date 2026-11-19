# Phase 98 — persistent storage and shutdown lifecycle

## Persistent web state

The web deployment uses YUKI_WORKSPACE as its persistent state root. The checked-in launcher stores SQLite databases and provider selection state under this directory, including authentication, conversations, inference jobs, and income-opportunity data. The web image creates /workspace and runs as UID/GID 10001:10001; a bind-mounted host directory must be provisioned for that identity before the container starts.

Example on a Linux host:

```bash
sudo install -d -m 0700 -o 10001 -g 10001 ./yuki-data
docker run --rm \
  --mount type=bind,src="$(pwd)/yuki-data",dst=/workspace \
  --publish 127.0.0.1:8000:8000 \
  yuki-web:local
```

Keep the published port on loopback when a reverse proxy runs on the same host. For a proxy in another container, use a private container network and publish no host port. Terminate TLS at the trusted proxy.

Do not use container writable-layer storage for production state. Back up the persistent volume using a database-aware procedure: SQLite databases may use WAL sidecar files while active. Quiesce Yuki or use SQLite's online backup mechanism; do not copy only the main SQLite database file while the application is writing. Test restoration into an isolated environment before relying on a backup.

## Durable sandbox isolation

Durable sandbox jobs remain disabled by default. Their jobs root is a separate operator-provisioned mode-0700 directory and must not be mixed with the general-purpose web workspace.

The web image does not mount the Docker socket. Enabling durable jobs requires a separately reviewed runtime design for access to a rootless daemon or a strictly constrained socket proxy. Do not mount an unrestricted host Docker socket into the web container: Docker daemon control can grant host-level authority and would defeat the container boundary.

## Shutdown lifecycle

ConversationRuntime.close() now marks itself closed before invoking owned cleanup, attempts both workforce and conversation application cleanup even if the first close operation raises, and re-raises the first cleanup error after all owned cleanup has been attempted. Repeated shutdown signals do not repeat partially completed cleanup.

A regression test simulates workforce cleanup failure and verifies that conversation storage still closes exactly once.

## Verification boundary

The source-level regression test has been added to the existing unittest discovery suite. It has not been independently executed in this environment. The latest GitHub workflow lookup returned no runs for the new commit at the time of this update; no CI pass, image build, backup restore, or production deployment is claimed.