"""Fail-closed gate for deployments whose side-effect path is not production-wired."""


def assert_production_runtime_supported(config: dict) -> None:
    """Refuse production startup until durable approval/ledger are wired end-to-end.

    The CLI currently routes skills through an in-process permission prompt and does
    not require the durable ApprovalCoordinator + execution ledger for every
    side-effect-capable command. Provider validation alone must not imply readiness.
    """
    if not isinstance(config, dict):
        raise TypeError("Runtime configuration must be a dictionary.")
    environment = str(config.get("environment", "development")).strip().lower()
    if environment != "production":
        return
    raise RuntimeError(
        "Production startup is blocked: durable approval, audit, and execution-ledger "
        "enforcement is not wired into every side-effect entry point. Use staging until "
        "the production execution path is integrated and verified."
    )
