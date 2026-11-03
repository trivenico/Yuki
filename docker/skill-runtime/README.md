# Isolated skill runtime image

This image is separate from the Yuki application image. It contains only the
Python interpreter and the fixed one-shot skill protocol runner.

## Build

Supply a verified immutable Python base image reference; mutable tags are not
accepted by the build/release process:

```sh
docker build \
  --build-arg PYTHON_BASE_IMAGE='python:3.11-slim@sha256:<verified-digest>' \
  -f docker/skill-runtime/Dockerfile \
  -t yuki-skill-runtime:local docker/skill-runtime
```

Resolve and review the base digest through the official image publisher before
building. Do not substitute a tag-only reference.

The resulting image must itself be pushed to a trusted registry, resolved to
its immutable manifest digest, scanned, and approved before a skill manifest
may reference it. The launcher uses `--pull=never`; the exact image must
already be present on the worker.

The runtime is not yet enabled in DurableSkillWorker. The host supervisor
must verify both content-addressed JSON Schemas, validate input before launch,
validate the response after launch, and reconcile durable launch state.
