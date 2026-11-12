"""Pure geometry primitives for image annotation clients.

The module is UI-framework independent. All persisted geometry uses normalized
image coordinates; viewport coordinates are transient and must never be stored.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Generic, TypeVar
import copy


_EPSILON = 1e-9


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ViewportTransform:
    """Aspect-preserving image-to-viewport transform with letterbox offsets."""

    scale: float
    offset_x: float
    offset_y: float
    image_width: int
    image_height: int
    viewport_width: float
    viewport_height: float

    @classmethod
    def fit(
        cls, image_width: int, image_height: int,
        viewport_width: float, viewport_height: float,
    ) -> "ViewportTransform":
        if (type(image_width) is not int or type(image_height) is not int
                or image_width <= 0 or image_height <= 0):
            raise ValueError("invalid_image_dimensions")
        values = (viewport_width, viewport_height)
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("invalid_viewport_dimensions")
        scale = min(viewport_width / image_width, viewport_height / image_height)
        rendered_width = image_width * scale
        rendered_height = image_height * scale
        return cls(
            scale=scale,
            offset_x=(viewport_width - rendered_width) / 2,
            offset_y=(viewport_height - rendered_height) / 2,
            image_width=image_width,
            image_height=image_height,
            viewport_width=float(viewport_width),
            viewport_height=float(viewport_height),
        )

    @property
    def image_bounds(self) -> Rect:
        return Rect(
            self.offset_x, self.offset_y,
            self.image_width * self.scale, self.image_height * self.scale,
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
            min(1.0, max(0.0, (point.x - self.offset_x) / bounds.width)),
            min(1.0, max(0.0, (point.y - self.offset_y) / bounds.height)),
        )


def _finite_point(point: Point) -> None:
    if (not isinstance(point, Point)
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in (point.x, point.y))):
        raise ValueError("invalid_point")


def _normalized_point(point: Point) -> None:
    _finite_point(point)
    if not (0 <= point.x <= 1 and 0 <= point.y <= 1):
        raise ValueError("point_out_of_bounds")


def bounding_box_from_drag(
    start: Point, end: Point, *, minimum_pixels: float = 1.0,
    image_width: int, image_height: int,
) -> Rect:
    """Create a normalized box from any drag direction; reject tiny gestures."""
    _normalized_point(start)
    _normalized_point(end)
    if (isinstance(minimum_pixels, bool)
            or not isinstance(minimum_pixels, (int, float))
            or not math.isfinite(minimum_pixels) or minimum_pixels <= 0):
        raise ValueError("invalid_minimum_box_size")
    if (type(image_width) is not int or type(image_height) is not int
            or image_width <= 0 or image_height <= 0):
        raise ValueError("invalid_image_dimensions")
    left, right = sorted((start.x, end.x))
    top, bottom = sorted((start.y, end.y))
    if ((right - left) * image_width < minimum_pixels
            or (bottom - top) * image_height < minimum_pixels):
        raise ValueError("bounding_box_too_small")
    return Rect(left, top, right - left, bottom - top)


def polygon_area(points: tuple[Point, ...] | list[Point]) -> float:
    """Return absolute normalized shoelace area."""
    if not isinstance(points, (tuple, list)) or len(points) < 3:
        raise ValueError("polygon_requires_three_points")
    for point in points:
        _normalized_point(point)
    return abs(sum(
        points[i].x * points[(i + 1) % len(points)].y
        - points[(i + 1) % len(points)].x * points[i].y
        for i in range(len(points))
    )) / 2.0


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _on_segment(a: Point, b: Point, point: Point) -> bool:
    return (
        min(a.x, b.x) - _EPSILON <= point.x <= max(a.x, b.x) + _EPSILON
        and min(a.y, b.y) - _EPSILON <= point.y <= max(a.y, b.y) + _EPSILON
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if ((o1 > _EPSILON and o2 < -_EPSILON or o1 < -_EPSILON and o2 > _EPSILON)
            and (o3 > _EPSILON and o4 < -_EPSILON or o3 < -_EPSILON and o4 > _EPSILON)):
        return True
    return (
        abs(o1) <= _EPSILON and _on_segment(a, b, c)
        or abs(o2) <= _EPSILON and _on_segment(a, b, d)
        or abs(o3) <= _EPSILON and _on_segment(c, d, a)
        or abs(o4) <= _EPSILON and _on_segment(c, d, b)
    )


def _reject_self_intersections(points: tuple[Point, ...]) -> None:
    count = len(points)
    for first in range(count):
        a, b = points[first], points[(first + 1) % count]
        for second in range(first + 1, count):
            # Adjacent edges share one intended endpoint.
            if second == first + 1 or (first == 0 and second == count - 1):
                continue
            c, d = points[second], points[(second + 1) % count]
            if _segments_intersect(a, b, c, d):
                raise ValueError("self_intersecting_polygon")


def validate_polygon(
    points: tuple[Point, ...] | list[Point], *, minimum_area: float = 1e-8,
) -> tuple[Point, ...]:
    """Validate a simple vertex sequence's basic invariants and nonzero area.

    Self-intersection checks are deliberately left to the interactive editor;
    this function guarantees bounds, distinct adjacent vertices, and area.
    """
    if not isinstance(points, (tuple, list)) or not 3 <= len(points) <= 10_000:
        raise ValueError("invalid_polygon_vertex_count")
    if (isinstance(minimum_area, bool)
            or not isinstance(minimum_area, (int, float))
            or not math.isfinite(minimum_area) or minimum_area <= 0):
        raise ValueError("invalid_minimum_polygon_area")
    result = tuple(points)
    for point in result:
        _normalized_point(point)
    if any(
        math.hypot(result[i].x - result[(i + 1) % len(result)].x,
                   result[i].y - result[(i + 1) % len(result)].y) <= _EPSILON
        for i in range(len(result))
    ):
        raise ValueError("duplicate_adjacent_polygon_vertex")
    _reject_self_intersections(result)
    if polygon_area(result) < minimum_area:
        raise ValueError("polygon_area_too_small")
    return result


T = TypeVar("T")


class SnapshotHistory(Generic[T]):
    """Bounded undo/redo history with defensive copies of mutable snapshots."""

    def __init__(self, initial: T, *, limit: int = 100):
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise ValueError("invalid_history_limit")
        self._limit = limit
        self._past: list[T] = []
        self._current: T = copy.deepcopy(initial)
        self._future: list[T] = []

    @property
    def current(self) -> T:
        return copy.deepcopy(self._current)

    @property
    def can_undo(self) -> bool:
        return bool(self._past)

    @property
    def can_redo(self) -> bool:
        return bool(self._future)

    def push(self, snapshot: T) -> None:
        self._past.append(self._current)
        if len(self._past) > self._limit:
            del self._past[0]
        self._current = copy.deepcopy(snapshot)
        self._future.clear()

    def undo(self) -> T:
        if not self._past:
            raise ValueError("nothing_to_undo")
        self._future.append(self._current)
        self._current = self._past.pop()
        return self.current

    def redo(self) -> T:
        if not self._future:
            raise ValueError("nothing_to_redo")
        self._past.append(self._current)
        if len(self._past) > self._limit:
            del self._past[0]
        self._current = self._future.pop()
        return self.current
