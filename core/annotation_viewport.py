"""Framework-independent zoom/pan camera for annotation image viewports.

The camera owns transient presentation state only. Annotation geometry remains
normalized to the source image and must never be persisted in viewport pixels.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from core.annotation_geometry import Point, Rect


@dataclass(frozen=True)
class AnnotationViewport:
    """Immutable camera with cursor-anchored zoom and recoverable pan bounds."""

    image_width: int
    image_height: int
    viewport_width: float
    viewport_height: float
    base_scale: float
    zoom: float
    offset_x: float
    offset_y: float
    min_zoom: float = 0.25
    max_zoom: float = 16.0
    visible_margin: float = 32.0

    @classmethod
    def fit(
        cls,
        image_width: int,
        image_height: int,
        viewport_width: float,
        viewport_height: float,
        *,
        min_zoom: float = 0.25,
        max_zoom: float = 16.0,
        visible_margin: float = 32.0,
    ) -> "AnnotationViewport":
        if (type(image_width) is not int or type(image_height) is not int
                or not 1 <= image_width <= 2_147_483_647
                or not 1 <= image_height <= 2_147_483_647):
            raise ValueError("invalid_image_dimensions")
        _positive_finite(viewport_width, "invalid_viewport_dimensions")
        _positive_finite(viewport_height, "invalid_viewport_dimensions")
        _positive_finite(min_zoom, "invalid_zoom_limits")
        _positive_finite(max_zoom, "invalid_zoom_limits")
        _positive_finite(visible_margin, "invalid_visible_margin")
        if min_zoom > 1 or max_zoom < 1 or min_zoom > max_zoom:
            raise ValueError("zoom_limits_must_include_fit")
        base_scale = min(viewport_width / image_width, viewport_height / image_height)
        maximum_scale = base_scale * max_zoom
        if (not math.isfinite(maximum_scale)
                or not math.isfinite(image_width * maximum_scale)
                or not math.isfinite(image_height * maximum_scale)):
            raise ValueError("viewport_scale_overflow")
        rendered_width = image_width * base_scale
        rendered_height = image_height * base_scale
        camera = cls(
            image_width=image_width,
            image_height=image_height,
            viewport_width=float(viewport_width),
            viewport_height=float(viewport_height),
            base_scale=base_scale,
            zoom=1.0,
            offset_x=(viewport_width - rendered_width) / 2,
            offset_y=(viewport_height - rendered_height) / 2,
            min_zoom=float(min_zoom),
            max_zoom=float(max_zoom),
            visible_margin=float(visible_margin),
        )
        return camera._with_offsets(camera.offset_x, camera.offset_y)

    @property
    def scale(self) -> float:
        return self.base_scale * self.zoom

    @property
    def image_bounds(self) -> Rect:
        return Rect(
            self.offset_x,
            self.offset_y,
            self.image_width * self.scale,
            self.image_height * self.scale,
        )

    def image_to_viewport(self, point: Point) -> Point:
        _normalized_point(point)
        return Point(
            self.offset_x + point.x * self.image_width * self.scale,
            self.offset_y + point.y * self.image_height * self.scale,
        )

    def viewport_to_image(self, point: Point) -> Point | None:
        _finite_point(point)
        bounds = self.image_bounds
        if (point.x < bounds.x or point.y < bounds.y
                or point.x > bounds.x + bounds.width
                or point.y > bounds.y + bounds.height):
            return None
        return Point(
            min(1.0, max(0.0, (point.x - bounds.x) / bounds.width)),
            min(1.0, max(0.0, (point.y - bounds.y) / bounds.height)),
        )

    def zoom_at(self, anchor: Point, factor: float) -> "AnnotationViewport":
        """Zoom around a viewport pixel, preserving its source-image point."""
        _finite_point(anchor)
        _positive_finite(factor, "invalid_zoom_factor")
        next_zoom = min(self.max_zoom, max(self.min_zoom, self.zoom * factor))
        if next_zoom == self.zoom:
            return self
        source_x = (anchor.x - self.offset_x) / self.scale
        source_y = (anchor.y - self.offset_y) / self.scale
        next_scale = self.base_scale * next_zoom
        return self._with_zoom_and_offsets(
            next_zoom,
            anchor.x - source_x * next_scale,
            anchor.y - source_y * next_scale,
        )

    def pan_by(self, delta_x: float, delta_y: float) -> "AnnotationViewport":
        """Pan in viewport pixels while keeping a recoverable image sliver visible."""
        _finite_number(delta_x, "invalid_pan_delta")
        _finite_number(delta_y, "invalid_pan_delta")
        return self._with_offsets(
            self.offset_x + delta_x,
            self.offset_y + delta_y,
        )

    def reset(self) -> "AnnotationViewport":
        """Return to the centered aspect-preserving fit."""
        return AnnotationViewport.fit(
            self.image_width,
            self.image_height,
            self.viewport_width,
            self.viewport_height,
            min_zoom=self.min_zoom,
            max_zoom=self.max_zoom,
            visible_margin=self.visible_margin,
        )

    def _with_zoom_and_offsets(
        self, zoom: float, offset_x: float, offset_y: float,
    ) -> "AnnotationViewport":
        candidate = AnnotationViewport(
            image_width=self.image_width,
            image_height=self.image_height,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            base_scale=self.base_scale,
            zoom=zoom,
            offset_x=offset_x,
            offset_y=offset_y,
            min_zoom=self.min_zoom,
            max_zoom=self.max_zoom,
            visible_margin=self.visible_margin,
        )
        return candidate._with_offsets(offset_x, offset_y)

    def _with_offsets(self, offset_x: float, offset_y: float) -> "AnnotationViewport":
        width = self.image_width * self.scale
        height = self.image_height * self.scale
        margin_x = min(self.visible_margin, width, self.viewport_width)
        margin_y = min(self.visible_margin, height, self.viewport_height)
        clamped_x = min(
            self.viewport_width - margin_x,
            max(margin_x - width, offset_x),
        )
        clamped_y = min(
            self.viewport_height - margin_y,
            max(margin_y - height, offset_y),
        )
        if clamped_x == self.offset_x and clamped_y == self.offset_y:
            return self
        return AnnotationViewport(
            image_width=self.image_width,
            image_height=self.image_height,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            base_scale=self.base_scale,
            zoom=self.zoom,
            offset_x=clamped_x,
            offset_y=clamped_y,
            min_zoom=self.min_zoom,
            max_zoom=self.max_zoom,
            visible_margin=self.visible_margin,
        )


def _finite_number(value: float, error: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(error)


def _positive_finite(value: float, error: str) -> None:
    _finite_number(value, error)
    if value <= 0:
        raise ValueError(error)


def _finite_point(point: Point) -> None:
    if not isinstance(point, Point):
        raise ValueError("invalid_point")
    _finite_number(point.x, "invalid_point")
    _finite_number(point.y, "invalid_point")


def _normalized_point(point: Point) -> None:
    _finite_point(point)
    if not 0 <= point.x <= 1 or not 0 <= point.y <= 1:
        raise ValueError("point_out_of_bounds")
