"""Explicit production worker composition over shared PostgreSQL state.

This factory is intentionally separate from the local SQLite bootstrap. It
requires the deployment-owned production security runtime, trusted identity
resolvers, a workload credential verifier, and a host credential issuer adapter.
It never runs migrations or starts background threads.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from automation.workflow_repository import ManagedWorkflowRepository
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime
from automation.worker_supervisor import WorkerSupervisor
from core.authorization import Principal
from core.production_security_runtime import ProductionSecurityRuntime


@dataclass
class ProductionWorkerRuntime:
    """Owns PostgreSQL task/delegation repositories, not the security runtime."""

    task_runtime: TaskRuntime
    worker: SpecialistTaskWorker
    supervisor: WorkerSupervisor
    workflow_store: ManagedWorkflowRepository
    delegation_authority: object
    security_runtime: ProductionSecurityRuntime
    _closed: bool = False

    def verify_readiness(self) -> None:
        if self._closed:
            raise RuntimeError("production_worker_runtime_closed")
        self.security_runtime.verify_readiness()
        for name, resource in (
            ("workflow_repository", self.workflow_store),
            ("delegation_authority", self.delegation_authority),
        ):
            check = getattr(resource, "check_ready", None)
            if not callable(check) or check() is not True:
                raise RuntimeError(name + "_not_ready")

    def run_cycle(self):
        self.verify_readiness()
        return self.supervisor.run_cycle()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors = []
        for resource in (self.delegation_authority, self.workflow_store):
            try:
                resource.close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("production_worker_shutdown_incomplete") from errors[0]


def create_production_worker_runtime(
    *,
    workflow_database_url: str,
    delegation_database_url: str,
    security_runtime: ProductionSecurityRuntime,
    policy_revision: str,
    workload_credential_verifier,
    workload_audience: str,
    workload_credential_provider: Callable[[str, str, str], str],
    worker_id: str,
    service_actor_id: str,
    handlers: Mapping[str, Callable[[str], str]],
    pool_min_size: int = 1,
    pool_max_size: int = 10,
    pool_timeout: float = 10.0,
    max_tasks_per_cycle: int = 10,
    recovery_limit: int = 50,
) -> ProductionWorkerRuntime:
    """Compose a PostgreSQL-only worker after host trust and DB migrations exist.

    The credential provider is a host-owned adapter that issues short-lived
    task/lease/delegation-bound credentials; model output and task payloads are
    never accepted as identity proof. Schema migrations 0007 and 0008 must be
    applied by the deployment migration role before this function is called.
    """
    if not isinstance(security_runtime, ProductionSecurityRuntime):
        raise TypeError("deployment_owned_production_security_runtime_required")
    for name, database_url in (
        ("workflow", workflow_database_url),
        ("delegation", delegation_database_url),
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("production_worker_" + name + "_database_url_invalid")
    if not isinstance(policy_revision, str) or not policy_revision.strip() or len(policy_revision) > 128:
        raise ValueError("production_worker_policy_revision_invalid")
    if not isinstance(workload_audience, str) or not workload_audience.strip() or len(workload_audience) > 256:
        raise ValueError("production_worker_audience_invalid")
    if not callable(workload_credential_provider):
        raise TypeError("trusted_workload_credential_provider_required")
    if not callable(getattr(workload_credential_verifier, "verify", None)):
        raise TypeError("trusted_workload_credential_verifier_required")
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
        raise ValueError("production_worker_identity_invalid")
    if not isinstance(service_actor_id, str) or not service_actor_id.strip() or len(service_actor_id) > 200:
        raise ValueError("production_worker_service_actor_invalid")
    if not isinstance(handlers, Mapping) or not handlers:
        raise ValueError("production_worker_handlers_required")
    if type(pool_min_size) is not int or type(pool_max_size) is not int or not 1 <= pool_min_size <= pool_max_size <= 50:
        raise ValueError("production_worker_pool_size_invalid")
    if isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float)) or not 1 <= pool_timeout <= 120:
        raise ValueError("production_worker_pool_timeout_invalid")
    if type(max_tasks_per_cycle) is not int or not 1 <= max_tasks_per_cycle <= 100:
        raise ValueError("production_worker_cycle_limit_invalid")
    if type(recovery_limit) is not int or not 1 <= recovery_limit <= 100:
        raise ValueError("production_worker_recovery_limit_invalid")

    principal_for_worker = security_runtime.security.require_worker
    principal_for_actor = security_runtime.security.require_actor
    service_principal = principal_for_actor(service_actor_id)
    if not isinstance(service_principal, Principal) or service_principal.principal_id != service_actor_id:
        raise PermissionError("trusted_worker_service_actor_resolution_failed")
    principal_for_worker(worker_id)

    from automation.postgres_workflow_store import PostgresWorkflowRunStore
    from core.postgres_delegation_authority import PostgresDelegationAuthority

    options = {
        "min_pool_size": pool_min_size,
        "max_pool_size": pool_max_size,
        "pool_timeout": pool_timeout,
    }
    workflow_store = None
    delegation_authority = None
    try:
        workflow_store = PostgresWorkflowRunStore(workflow_database_url, **options)
        if not isinstance(workflow_store, ManagedWorkflowRepository):
            raise TypeError("production_workflow_repository_contract_incomplete")
        delegation_authority = PostgresDelegationAuthority(delegation_database_url, **options)
        if workflow_store.database_identity != delegation_authority.database_identity:
            raise RuntimeError("workflow_and_delegation_database_mismatch")
        if workflow_store.database_identity != security_runtime.work_leases.database_identity:
            # Security leases are a different table but must share the same
            # physical database for one coherent production deployment.
            raise RuntimeError("worker_and_security_database_mismatch")

        gateway = security_runtime.security.authorization_gateway
        task_runtime = TaskRuntime(
            workflow_store,
            authorization_gateway=gateway,
            principal_for_actor=principal_for_actor,
            policy_revision=policy_revision,
            workload_credential_verifier=workload_credential_verifier,
            workload_audience=workload_audience,
            delegation_authority=delegation_authority,
            require_workload_delegation=True,
        )
        worker = SpecialistTaskWorker(
            task_runtime,
            worker_id=worker_id,
            service_actor_id=service_actor_id,
            handlers=handlers,
            authorization_gateway=gateway,
            principal_for_worker=principal_for_worker,
            policy_revision=policy_revision,
            workload_credential_provider=workload_credential_provider,
        )
        supervisor = WorkerSupervisor(
            task_runtime, worker,
            max_tasks_per_cycle=max_tasks_per_cycle,
            recovery_limit=recovery_limit,
            max_workers=1,
        )
        composed = ProductionWorkerRuntime(
            task_runtime=task_runtime,
            worker=worker,
            supervisor=supervisor,
            workflow_store=workflow_store,
            delegation_authority=delegation_authority,
            security_runtime=security_runtime,
        )
        composed.verify_readiness()
        return composed
    except Exception:
        for resource in (delegation_authority, workflow_store):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        raise


@dataclass
class ProductionSpecialistRuntime:
    """Owns the shared PostgreSQL specialist control-plane adapters."""

    worker_runtime: ProductionWorkerRuntime
    coordinator: object
    plan_repository: object
    agent_registry: object
    _closed: bool = False

    def verify_readiness(self) -> None:
        if self._closed:
            raise RuntimeError("production_specialist_runtime_closed")
        self.worker_runtime.verify_readiness()
        for name, resource in (
            ("agent_plan_repository", self.plan_repository),
            ("specialist_agent_registry", self.agent_registry),
        ):
            check = getattr(resource, "check_ready", None)
            if not callable(check) or check() is not True:
                raise RuntimeError(name + "_not_ready")
            if resource.database_identity != self.worker_runtime.workflow_store.database_identity:
                raise RuntimeError(name + "_database_mismatch")

    def run_cycle(self):
        """Run one supervised task cycle only after all specialist stores are ready."""
        self.verify_readiness()
        return self.worker_runtime.run_cycle()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors = []
        for resource in (self.agent_registry, self.plan_repository):
            try:
                resource.close()
            except Exception as exc:
                errors.append(exc)
        try:
            self.worker_runtime.close()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError("production_specialist_shutdown_incomplete") from errors[0]


def create_production_specialist_runtime(
    *,
    plan_database_url: str,
    registry_database_url: str,
    initial_agents: Iterable[object],
    workload_principal_for_agent: Callable[[str], Principal],
    workflow_database_url: str,
    delegation_database_url: str,
    security_runtime: ProductionSecurityRuntime,
    policy_revision: str,
    workload_credential_verifier,
    workload_audience: str,
    workload_credential_provider: Callable[[str, str, str], str],
    worker_id: str,
    service_actor_id: str,
    handlers: Mapping[str, Callable[[str], str]],
    pool_min_size: int = 1,
    pool_max_size: int = 10,
    pool_timeout: float = 10.0,
    max_tasks_per_cycle: int = 10,
    recovery_limit: int = 50,
) -> ProductionSpecialistRuntime:
    """Compose worker, plans, and registry against one shared PostgreSQL endpoint.

    The caller supplies trusted agent workload-principal resolution and the
    reviewed initial manifest definitions. No SQLite persistence is opened:
    the coordinator receives explicit PostgreSQL adapters and an inert
    in-memory compatibility path. This composes the control plane only; it
    does not start an outbox dispatcher or claim distributed execution readiness.
    """
    if not callable(workload_principal_for_agent):
        raise TypeError("trusted_specialist_workload_resolver_required")
    agents = tuple(initial_agents)
    if not agents or len(agents) > 64:
        raise ValueError("production_specialist_initial_agents_invalid")

    worker_runtime = create_production_worker_runtime(
        workflow_database_url=workflow_database_url,
        delegation_database_url=delegation_database_url,
        security_runtime=security_runtime,
        policy_revision=policy_revision,
        workload_credential_verifier=workload_credential_verifier,
        workload_audience=workload_audience,
        workload_credential_provider=workload_credential_provider,
        worker_id=worker_id,
        service_actor_id=service_actor_id,
        handlers=handlers,
        pool_min_size=pool_min_size,
        pool_max_size=pool_max_size,
        pool_timeout=pool_timeout,
        max_tasks_per_cycle=max_tasks_per_cycle,
        recovery_limit=recovery_limit,
    )
    plan_repository = None
    agent_registry = None
    try:
        from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
        from automation.postgres_agent_plan_adapter import PostgresAgentPlanRepositoryAdapter
        from automation.postgres_agent_plan_repository import PostgresAgentPlanRepository
        from automation.postgres_specialist_agent_registry import PostgresSpecialistAgentRegistry

        if any(not isinstance(agent, SpecialistAgent) for agent in agents):
            raise TypeError("production_specialist_manifest_entries_invalid")
        options = {
            "min_pool_size": pool_min_size,
            "max_pool_size": pool_max_size,
            "pool_timeout": pool_timeout,
        }
        plan_repository = PostgresAgentPlanRepository(plan_database_url, **options)
        agent_registry = PostgresSpecialistAgentRegistry(
            registry_database_url,
            agent_factory=SpecialistAgent,
            validate_agent=SpecialistAgentCoordinator._validate_agent,
            **options,
        )
        expected_identity = worker_runtime.workflow_store.database_identity
        if plan_repository.database_identity != expected_identity:
            raise RuntimeError("agent_plan_database_mismatch")
        if agent_registry.database_identity != expected_identity:
            raise RuntimeError("specialist_registry_database_mismatch")

        coordinator = SpecialistAgentCoordinator(
            worker_runtime.task_runtime,
            ":memory:",
            agents,
            authorization_gateway=security_runtime.security.authorization_gateway,
            principal_for_actor=security_runtime.security.require_actor,
            workload_principal_for_agent=workload_principal_for_agent,
            policy_revision=policy_revision,
            plan_repository=PostgresAgentPlanRepositoryAdapter(plan_repository),
            agent_registry=agent_registry,
        )
        composed = ProductionSpecialistRuntime(
            worker_runtime=worker_runtime,
            coordinator=coordinator,
            plan_repository=plan_repository,
            agent_registry=agent_registry,
        )
        composed.verify_readiness()
        return composed
    except Exception:
        for resource in (agent_registry, plan_repository):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        worker_runtime.close()
        raise
