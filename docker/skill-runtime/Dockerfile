# Build with an immutable base reference, for example:
# docker build --build-arg PYTHON_BASE_IMAGE='python:3.11-slim@sha256:<verified-digest>' .
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0
RUN mkdir -p /opt/yuki/bin /yuki/skill \
    && chown -R 65532:65532 /yuki
COPY --chmod=0555 skill_runner.py /opt/yuki/bin/skill-runner
USER 65532:65532
WORKDIR /yuki/skill
ENTRYPOINT ["/opt/yuki/bin/skill-runner", "--protocol-v1"]
