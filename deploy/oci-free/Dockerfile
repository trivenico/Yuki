FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 YUKI_GATEWAY_HOST=0.0.0.0 YUKI_GATEWAY_PORT=8765 YUKI_GATEWAY_DATABASE=/var/lib/yuki/gateway.sqlite3
WORKDIR /app
ARG INSTALL_OCI_AUTH=false
COPY --chown=root:root requirements-oci.txt /app/requirements-oci.txt
RUN case "$INSTALL_OCI_AUTH" in true) pip install --no-cache-dir -r /app/requirements-oci.txt ;; false) ;; *) echo "INSTALL_OCI_AUTH must be true or false" >&2; exit 1 ;; esac
RUN groupadd --system --gid 10001 yuki && useradd --system --uid 10001 --gid yuki --home-dir /nonexistent yuki && install -d -o yuki -g yuki -m 0750 /var/lib/yuki
COPY --chown=root:root cloud/ /app/cloud/
COPY --chown=root:root automation/ /app/automation/
COPY --chown=root:root core/ /app/core/
COPY --chown=root:root providers/ /app/providers/
COPY --chown=root:root yuki_providers.py /app/yuki_providers.py
COPY --chown=root:root config.yaml /app/config.yaml
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()" || exit 1
CMD ["python", "-m", "cloud.gateway_server"]
