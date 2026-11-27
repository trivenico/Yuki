"""Credential-independent PostgreSQL endpoint identity helpers.

The digest deliberately excludes user/password and TLS credential material so
separate least-privilege roles can be recognized as targeting one database
endpoint. It is an endpoint comparison, not cryptographic proof of cluster
identity; deployments should still pin trusted DNS/network and TLS settings.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict


def postgres_database_identity(database_url: str) -> str:
    """Return a stable, non-secret digest for a PostgreSQL logical endpoint."""
    if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
        raise ValueError("postgres_database_url_invalid")
    candidate = database_url.strip()
    # URI input must identify an explicit host; libpq otherwise supplies a
    # platform-dependent local-socket default that is not a stable endpoint.
    if candidate.lower().startswith(("postgres://", "postgresql://")):
        try:
            if not urlsplit(candidate).hostname:
                raise ValueError("postgres_database_endpoint_host_required")
        except ValueError as exc:
            raise ValueError("postgres_database_url_invalid") from exc
    try:
        values = conninfo_to_dict(candidate)
    except Exception as exc:
        raise ValueError("postgres_database_url_invalid") from exc

    endpoint = {
        "service": values.get("service") or None,
        "host": values.get("host") or None,
        "hostaddr": values.get("hostaddr") or None,
        "port": values.get("port") or None,
        "dbname": values.get("dbname") or None,
    }
    if not endpoint["service"] and not endpoint["host"] and not endpoint["hostaddr"]:
        # libpq's default socket location is platform-specific; refusing to
        # guess is safer than treating unrelated hosts as the same database.
        raise ValueError("postgres_database_endpoint_host_required")
    if not endpoint["dbname"]:
        raise ValueError("postgres_database_endpoint_dbname_required")
    encoded = json.dumps(endpoint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "postgres-endpoint:" + hashlib.sha256(encoded).hexdigest()
