# Specialist catalog provider wiring

The specialist catalog is routing metadata; the existing specialist worker
already owns the four reviewed, bounded text-only prompt roles. This adapter
connects the two without inventing a second worker or prompt framework.

## Example

```python
from automation.specialist_provider_wiring import build_catalog_provider_handlers

handlers = build_catalog_provider_handlers(
    provider_manager,
    role_map={
        "research": "research",
        "engineering": "coding",
        "testing": "testing",
        "documentation": "reporting",
    },
    provider_preferences={
        "research": ("ollama",),
        "engineering": ("ollama",),
    },
    allow_cloud=False,
)
```

Role mappings are explicit and limited to the existing worker roles:
`research`, `coding`, `testing`, and `reporting`. The adapter does not
infer roles from agent names or capabilities. Unknown catalog IDs, unsupported
roles, empty configuration, and excessive mappings fail closed.

Provider selection remains inside the existing provider manager. Cloud use is
disabled unless the caller explicitly sets `allow_cloud=True`; a preference
list is strict and does not grant fallback. The returned handlers remain
text-only and do not execute code, call arbitrary tools, or modify a workspace.

## Integration boundary

This helper constructs handlers only. The application still needs to register
them against the existing `SpecialistTaskWorker` using matching catalog IDs,
configure the coordinator and worker authorization policies, and preserve
task approval and lease handling.

## Tests

```bash
python -m unittest tests.test_specialist_provider_wiring
```
