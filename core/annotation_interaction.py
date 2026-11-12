"""Framework-independent pointer interaction state for image annotations.

Rendering clients own pointer capture and viewport conversion. This controller
owns gesture identity, tool state, normalized geometry, and bounded history.
"""
from __future__ import annotations

import math
from typing import Any

from core.annotation_geometry import (
    Point, Rect, SnapshotHistory, bounding_box_from_drag, validate_polygon,
)


class AnnotationInteraction:
    """State machine for box drags and polygon vertex editing."""

    def __init__(
        self, image_width: int, image_height: int, *, history_limit: int = 100,
        minimum_box_pixels: float = 1.0,
    ) -> None:
        if (type(image_width) is not int or type(image_height) is not int
                or image_width <= 0 or image_height <= 0):
            raise ValueError("invalid_image_dimensions")
        if (isinstance(minimum_box_pixels, bool)
                or not isinstance(minimum_box_pixels, (int, float))
                or not math.isfinite(minimum_box_pixels) or minimum_box_pixels <= 0):
            raise ValueError("invalid_minimum_box_size")
        self.image_width = image_width
        self.image_height = image_height
        self.minimum_box_pixels = float(minimum_box_pixels)
        self.tool = "box"
        self._pointer_id: int | str | None = None
        self._drag_start: Point | None = None
        self._drag_current: Point | None = None
        self._polygon_draft: list[Point] = []
        initial = {"boxes": [], "polygons": []}
        self._history = SnapshotHistory(initial, limit=history_limit)

    @property
    def pointer_active(self) -> bool:
        return self._pointer_id is not None

    @property
    def drag_preview(self) -> tuple[Point, Point] | None:
        if self._drag_start is None or self._drag_current is None:
            return None
        return self._drag_start, self._drag_current

    @property
    def polygon_draft(self) -> tuple[Point, ...]:
        return tuple(self._polygon_draft)

    @property
    def document(self) -> dict[str, Any]:
        return self._history.current

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    def set_tool(self, tool: str) -> None:
        if tool not in {"box", "polygon"}:
            raise ValueError("unsupported_annotation_tool")
        if self.pointer_active:
            raise ValueError("pointer_gesture_active")
        self.tool = tool
        self._polygon_draft.clear()

    def pointer_down(self, pointer_id: int | str, point: Point) -> None:
        if self.tool != "box":
            raise ValueError("box_tool_not_selected")
        if self.pointer_active:
            raise ValueError("pointer_gesture_active")
        if pointer_id is None or isinstance(pointer_id, bool) or not isinstance(pointer_id, (int, str)):
            raise ValueError("invalid_pointer_id")
        # Reject non-finite/out-of-image coordinates before capturing the pointer.
        _validate_point(point)
        self._pointer_id = pointer_id
        self._drag_start = point
        self._drag_current = point

    def pointer_move(self, pointer_id: int | str, point: Point) -> None:
        self._require_pointer(pointer_id)
        _validate_point(point)
        self._drag_current = point

    def pointer_up(self, pointer_id: int | str, point: Point, *, label: str) -> Rect:
        self._require_pointer(pointer_id)
        try:
            _validate_point(point)
            if not isinstance(label, str) or not label.strip():
                raise ValueError("annotation_label_required")
            assert self._drag_start is not None
            rect = bounding_box_from_drag(
                self._drag_start, point,
                minimum_pixels=self.minimum_box_pixels,
                image_width=self.image_width, image_height=self.image_height,
            )
            snapshot = self.document
            snapshot["boxes"].append({
                "label": label.strip(), "x": rect.x, "y": rect.y,
                "width": rect.width, "height": rect.height,
            })
            self._history.push(snapshot)
            return rect
        finally:
            self._release_pointer()

    def add_polygon_vertex(self, point: Point) -> None:
        if self.tool != "polygon":
            raise ValueError("polygon_tool_not_selected")
        _validate_point(point)
        self._polygon_draft.append(point)
        if len(self._polygon_draft) > 2_048:
            self._polygon_draft.pop()
            raise ValueError("invalid_polygon_vertex_count")

    def finish_polygon(self, *, label: str) -> tuple[Point, ...]:
        if self.tool != "polygon":
            raise ValueError("polygon_tool_not_selected")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("annotation_label_required")
        points = validate_polygon(self._polygon_draft)
        snapshot = self.document
        snapshot["polygons"].append({
            "label": label.strip(),
            "points": [[point.x, point.y] for point in points],
        })
        self._history.push(snapshot)
        self._polygon_draft.clear()
        return points

    def cancel_gesture(self) -> None:
        self._release_pointer()
        self._polygon_draft.clear()

    def undo(self) -> dict[str, Any]:
        self._release_pointer()
        self._polygon_draft.clear()
        return self._history.undo()

    def redo(self) -> dict[str, Any]:
        self._release_pointer()
        self._polygon_draft.clear()
        return self._history.redo()

    def _require_pointer(self, pointer_id: int | str) -> None:
        if not self.pointer_active or pointer_id != self._pointer_id:
            raise ValueError("pointer_identity_mismatch")

    def _release_pointer(self) -> None:
        self._pointer_id = None
        self._drag_start = None
        self._drag_current = None


def _validate_point(point: Point) -> None:
    if not isinstance(point, Point):
        raise ValueError("invalid_point")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in (point.x, point.y)
    ):
        raise ValueError("invalid_point")
    if not 0 <= point.x <= 1 or not 0 <= point.y <= 1:
        raise ValueError("point_out_of_bounds")
