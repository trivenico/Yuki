"""Renderer-independent spatial data contracts."""

from spatial.source_registry import (
    Freshness, LicensePolicy, Permission, Sensitivity, SpatialRecord,
    SpatialSource, SpatialSourceRegistry, validate_source,
)

__all__ = [
    "Freshness", "LicensePolicy", "Permission", "Sensitivity", "SpatialRecord",
    "SpatialSource", "SpatialSourceRegistry", "validate_source",
]
