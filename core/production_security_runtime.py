"""Fail-closed composition and lifecycle for the shared production security stack.

This module is a host bootstrap boundary. It accepts trusted policy, identity,
and operator callbacks; it never derives privileges from requests or model output.
It intentionally supports PostgreSQL only for multi-replica deployments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit_factory import (
    create_authorization_audit_repository,
    close_authorization_audit_repository,
)
from core.production_security import ProductionSecurityContext
from core.security_event_monitor_factory import (
    create_security_event_monitor,
    close_security_event_monitor,
)
from core.workload_key_registry_factory import (
    create_workload_key_registry,
    close_workload_key_registry,
)


@dataclass
class ProductionSecurityRuntime:
    """Owned security dependencies with deterministic reverse-order shutdown."""

    security: ProductionSecurityContext
    workload_key_registry: object
    authorization_audit_reader: object
    workload_key_audit_reader: object
    security_monitor: object
    ingestion_cursors: object
    ingestor: object
    alert_outbox: object
    work_leases: object
    _closed: bool = False

    def verify_readiness(self) -> None:
        """Recheck every durable dependency before accepting protected traffic."""
        self.security.verify_audit_integrity()
        for name, resource in (
            ("workload_key_registry", self.workload_key_registry),
            ("security_monitor", self.security_monitor),
            ("ingestion_cursors", self.ingestion_cursors),
            ("alert_outbox", self.alert_outbox),
            ("work_leases", self.work_leases),
        ):
            check = getattr(resource, "check_ready", None)
            if not callable(check) or check() is not True:
                raise RuntimeError(name + "_not_ready")

    def ingest_authorization_page(self, worker_id: str):
        return self.ingestor.ingest_authorization_page(
            self.authorization_audit_reader, worker_id,
        )

    def ingest_key_lifecycle_page(self, worker_id: str):
        return self.ingestor.ingest_key_lifecycle_page(worker_id)

    def sync_alerts(self, operator: str, *, limit: int = 100):
        return self.alert_outbox.sync_incidents(self.security_monitor, operator, limit=limit)

    def deliver_alert_one(self, worker_id: str, sender: Callable):
        """Deliver one outbox item using a host-supplied provider adapter."""
        return self.alert_outbox.deliver_one(worker_id, sender)

    def operational_snapshot(self, operator: str):
        """Return authorized, payload-free recovery state for operator telemetry."""
        if self._closed:
            raise RuntimeError("production_security_runtime_closed")
        alerts = self.alert_outbox.health_snapshot(operator)
        return {
            "ingestion": self.ingestion_cursors.snapshot(),
            "alerts": alerts,
        }

    def close(self) -> None:
        """Close all owned pools even if one close operation raises."""
        if self._closed:
            return
        self._closed = True
        errors = []
        for resource, closer in (
            (self.work_leases, lambda value: value.close()),
            (self.alert_outbox, lambda value: value.close()),
            (self.workload_key_audit_reader, lambda value: value.close()),
            (self.authorization_audit_reader, lambda value: value.close()),
            (self.ingestion_cursors, lambda value: value.close()),
            (self.security_monitor, close_security_event_monitor),
            (self.workload_key_registry, close_workload_key_registry),
            (self.security.authorization_gateway.audit_writer, close_authorization_audit_repository),
        ):
            try:
                if not callable(closer):
                    raise TypeError("security_resource_close_required")
                closer(resource)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("production_security_shutdown_incomplete") from errors[0]


def create_production_security_runtime(
    config: dict,
    *,
    policy,
    audit_anchor,
    principal_for_actor: Callable,
    principal_for_worker: Callable,
    policy_revision: str,
    authorize_admin: Callable[[str], bool],
    authorize_operator: Callable[[str], bool],
    key_audit_operator: str,
) -> ProductionSecurityRuntime:
    """Construct, validate, and own all shared security repositories.

    A PostgreSQL backend is mandatory here: combining shared cursors with local
    SQLite monitor or key-registry state would split security state by replica.
    Startup is all-or-nothing; any partially created pools are closed in reverse
    order before the original exception is re-raised.
    """
    if not isinstance(config, dict):
        raise TypeError("production_security_config_required")
    if config.get("storage_backend") != "postgres":
        raise ValueError("production_security_runtime_requires_postgres")
    database_urls = config.get("database_urls")
    required_identities = (
        "authorization_audit", "authorization_audit_reader",
        "workload_key_registry", "workload_key_audit_reader", "security_monitor",
        "ingestion_cursors", "alert_outbox", "work_leases",
    )
    if not isinstance(database_urls, dict):
        raise ValueError("production_security_database_urls_required")
    invalid_identities = [
        name for name in required_identities
        if not isinstance(database_urls.get(name), str)
        or not database_urls[name].strip()
        or len(database_urls[name]) > 4096
    ]
    if invalid_identities:
        raise ValueError("production_security_database_identity_required:" + ",".join(invalid_identities))
    if len({database_urls[name].strip() for name in required_identities}) != len(required_identities):
        raise ValueError("production_security_database_identities_must_be_distinct")
    for callback, label in (
        (authorize_admin, "trusted_workload_key_admin_authorizer_required"),
        (authorize_operator, "trusted_security_operator_authorizer_required"),
        (principal_for_actor, "trusted_actor_resolver_required"),
        (principal_for_worker, "trusted_worker_resolver_required"),
    ):
        if not callable(callback):
            raise TypeError(label)
    if not isinstance(key_audit_operator, str) or not key_audit_operator.strip() or len(key_audit_operator) > 256:
        raise ValueError("trusted_key_audit_operator_required")

    from core.postgres_security_event_ingestion import PostgresSecurityEventIngestionCursors
    from core.postgres_security_audit_readers import (
        PostgresAuthorizationAuditReader, PostgresWorkloadKeyAuditReader,
    )
    from core.postgres_security_alert_outbox import PostgresSecurityAlertOutbox
    from core.postgres_security_lease_store import PostgresSecurityLeaseStore
    from core.security_event_ingestion import DurableSecurityEventIngestor

    pool = {
        "database_pool_min": config.get("database_pool_min", 1),
        "database_pool_max": config.get("database_pool_max", 10),
        "database_pool_timeout": config.get("database_pool_timeout", 10.0),
    }
    # Do not fan out the complete credential map to repository factories.
    # Each factory receives only its own DSN; otherwise every constructor would
    # hold every service credential even though it opens only one pool.
    factory_config = dict(config)
    factory_config.pop("database_urls", None)
    created = []
    try:
        audit_config = dict(factory_config)
        audit_config["database_url"] = database_urls["authorization_audit"]
        audit = create_authorization_audit_repository(audit_config)
        created.append((audit, close_authorization_audit_repository))
        gateway = AuthorizationGateway(policy, audit=audit)
        security = ProductionSecurityContext(
            authorization_gateway=gateway,
            principal_for_actor=principal_for_actor,
            principal_for_worker=principal_for_worker,
            policy_revision=policy_revision,
            audit_anchor=audit_anchor,
            audit_checkpoint_max_age_seconds=config.get("audit_checkpoint_max_age_seconds", 300.0),
        )

        registry_config = dict(factory_config)
        registry_config["database_url"] = database_urls["workload_key_registry"]
        registry = create_workload_key_registry(
            registry_config, authorize_admin=authorize_admin,
        )
        created.append((registry, close_workload_key_registry))

        authorization_reader = PostgresAuthorizationAuditReader(
            database_urls["authorization_audit_reader"],
            min_pool_size=pool["database_pool_min"],
            max_pool_size=pool["database_pool_max"],
            pool_timeout=pool["database_pool_timeout"],
        )
        created.append((authorization_reader, lambda value: value.close()))
        key_audit_reader = PostgresWorkloadKeyAuditReader(
            database_urls["workload_key_audit_reader"],
            min_pool_size=pool["database_pool_min"],
            max_pool_size=pool["database_pool_max"],
            pool_timeout=pool["database_pool_timeout"],
        )
        created.append((key_audit_reader, lambda value: value.close()))

        monitor_config = dict(factory_config)
        monitor_config["database_url"] = database_urls["security_monitor"]
        monitor_config["security_failure_threshold"] = config.get("security_failure_threshold", 5)
        monitor_config["security_window_seconds"] = config.get("security_window_seconds", 300)
        monitor = create_security_event_monitor(
            monitor_config, authorize_operator=authorize_operator,
        )
        created.append((monitor, close_security_event_monitor))

        cursors = PostgresSecurityEventIngestionCursors(
            database_urls["ingestion_cursors"], min_pool_size=pool["database_pool_min"],
            max_pool_size=pool["database_pool_max"],
            pool_timeout=pool["database_pool_timeout"],
            lease_seconds=config.get("ingestion_lease_seconds", 60),
        )
        created.append((cursors, lambda value: value.close()))

        ingestor = DurableSecurityEventIngestor(
            monitor, cursors, key_registry=key_audit_reader,
            key_audit_operator=key_audit_operator,
            batch_size=config.get("ingestion_batch_size", 500),
        )

        outbox = PostgresSecurityAlertOutbox(
            database_urls["alert_outbox"], authorize_operator=authorize_operator,
            base_retry_seconds=config.get("alert_base_retry_seconds", 5),
            max_retry_seconds=config.get("alert_max_retry_seconds", 3600),
            max_attempts=config.get("alert_max_attempts", 12),
            lease_seconds=config.get("alert_lease_seconds", 60),
            min_pool_size=pool["database_pool_min"],
            max_pool_size=pool["database_pool_max"],
            pool_timeout=pool["database_pool_timeout"],
        )
        created.append((outbox, lambda value: value.close()))

        leases = PostgresSecurityLeaseStore(
            database_urls["work_leases"], min_pool_size=pool["database_pool_min"],
            max_pool_size=pool["database_pool_max"],
            pool_timeout=pool["database_pool_timeout"],
        )
        created.append((leases, lambda value: value.close()))

        runtime = ProductionSecurityRuntime(
            security=security, workload_key_registry=registry,
            authorization_audit_reader=authorization_reader,
            workload_key_audit_reader=key_audit_reader,
            security_monitor=monitor, ingestion_cursors=cursors,
            ingestor=ingestor, alert_outbox=outbox, work_leases=leases,
        )
        # The audit factory has already verified the chain and the context
        # constructor has verified its independent anchor. Check each remaining
        # repository once here to avoid redundant full-chain scans at startup.
        for name, resource in (
            ("workload_key_registry", registry),
            ("authorization_audit_reader", authorization_reader),
            ("workload_key_audit_reader", key_audit_reader),
            ("security_monitor", monitor),
            ("ingestion_cursors", cursors),
            ("alert_outbox", outbox),
            ("work_leases", leases),
        ):
            check = getattr(resource, "check_ready", None)
            if not callable(check) or check() is not True:
                raise RuntimeError(name + "_not_ready")
        return runtime
    except Exception:
        for resource, close in reversed(created):
            try:
                close(resource)
            except Exception:
                pass
        raise
