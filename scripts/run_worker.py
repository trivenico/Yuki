"""Compatibility wrapper; installed Linux releases use automation.worker_service."""
from automation.worker_service import (
    load_factory as _load_factory,
    load_production_runtime,
    main,
    run_worker_service,
)

__all__ = ["_load_factory", "load_production_runtime", "run_worker_service", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
