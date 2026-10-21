"""Fail-closed gate for deployments whose side-effect path is not production-wired."""


_NON_PRODUCTION_ENVIRONMENTS = frozenset({"development", "test", "staging"})


def assert_production_runtime_supported(config: dict) -> None:
    """Allow known non-production environments; block production and unknown values.

    The CLI still has side-effect-capable commands outside the durable
    ApprovalCoordinator + execution-ledger path. This guard is defense-in-depth
    and must remain until those commands are integrated.
    """
    if not isinstance(config, dict):
        raise TypeError("Runtime configuration must be a dictionary.")

    raw_environment = config.get("environment", "development")
    if not isinstance(raw_environment, str) or not raw_environment.strip():
        raise RuntimeError("Runtime environment must be an explicit non-empty string.")

    environment = raw_environment.strip().lower()
    if environment in _NON_PRODUCTION_ENVIRONMENTS:
        return

    if environment == "production":
        reason = (
            "Production startup is blocked: durable approval, audit, and "
            "execution-ledger enforcement is not wired into every side-effect "
            "entry point. Use staging until the production execution path is "
            "integrated and verified."
        )
    else:
        reason = (
            f"Runtime startup is blocked for unrecognized environment {environment!r}; "
            "use development, test, staging, or production explicitly."
        )
    raise RuntimeError(reason)
