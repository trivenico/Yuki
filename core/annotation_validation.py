"""Strict task-family validation for human annotation submissions.

Coordinates use normalized [0, 1] image space. Timestamps are seconds.
Unknown task types intentionally retain the legacy custom JSON-object contract.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from core.annotation_geometry import Point, validate_polygon


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_" + field)
    return value


def _keys(value: Mapping[str, Any], required: set[str], optional: set[str], field: str) -> None:
    if required - set(value) or set(value) - required - optional:
        raise ValueError("invalid_" + field + "_fields")


def _string(value: Any, field: str, *, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("invalid_" + field)
    return value


def _number(value: Any, field: str, *, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("invalid_" + field)
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError("out_of_range_" + field)
    return float(value)


def _label(value: Any, rubric: Mapping[str, Any]) -> str:
    label = _string(value, "label", maximum=256)
    allowed = rubric.get("labels")
    if isinstance(allowed, list) and allowed and label not in allowed:
        if rubric.get("allow_unknown") is not True or label.lower() not in {"unknown", "other", "unsure"}:
            raise ValueError("label_not_in_project_rubric")
    return label


def _bbox(item: Any, rubric: Mapping[str, Any]) -> None:
    box = _object(item, "bounding_box")
    _keys(box, {"label", "x", "y", "width", "height"}, set(), "bounding_box")
    _label(box["label"], rubric)
    x = _number(box["x"], "box_x", minimum=0, maximum=1)
    y = _number(box["y"], "box_y", minimum=0, maximum=1)
    width = _number(box["width"], "box_width", minimum=0, maximum=1)
    height = _number(box["height"], "box_height", minimum=0, maximum=1)
    if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ValueError("bounding_box_out_of_bounds")


def _segments(value: Any, *, speaker_required: bool) -> None:
    if not isinstance(value, list) or not value or len(value) > 10000:
        raise ValueError("invalid_audio_segments")
    for item in value:
        segment = _object(item, "audio_segment")
        required = {"start", "end", "text"} | ({"speaker"} if speaker_required else set())
        _keys(segment, required, set() if speaker_required else {"speaker"}, "audio_segment")
        start = _number(segment["start"], "segment_start", minimum=0)
        end = _number(segment["end"], "segment_end", minimum=0)
        if end <= start:
            raise ValueError("audio_segment_end_must_follow_start")
        _string(segment["text"], "segment_text", maximum=12000)
        if "speaker" in segment:
            _string(segment["speaker"], "speaker", maximum=128)


def validate_annotation_payload(task_type: str, payload: Mapping[str, Any],
                                rubric: Mapping[str, Any] | None = None) -> None:
    """Raise ValueError unless payload matches the selected annotation task family."""
    data = _object(payload, "annotation_payload")
    rules = _object(rubric or {}, "project_rubric")
    if task_type == "custom":
        return
    if task_type in {"image_classification", "text_classification"}:
        _keys(data, {"label"}, {"confidence", "rationale"}, "classification")
        _label(data["label"], rules)
        if "confidence" in data:
            _number(data["confidence"], "confidence", minimum=0, maximum=1)
        if "rationale" in data:
            _string(data["rationale"], "rationale")
    elif task_type == "bounding_boxes":
        _keys(data, {"boxes"}, set(), "bounding_boxes")
        boxes = data["boxes"]
        if not isinstance(boxes, list) or not boxes or len(boxes) > 10000:
            raise ValueError("invalid_bounding_boxes")
        for item in boxes:
            _bbox(item, rules)
    elif task_type == "polygon_segmentation":
        _keys(data, {"polygons"}, set(), "polygon_segmentation")
        polygons = data["polygons"]
        if not isinstance(polygons, list) or not polygons or len(polygons) > 10000:
            raise ValueError("invalid_polygons")
        for item in polygons:
            polygon = _object(item, "polygon")
            _keys(polygon, {"label", "points"}, set(), "polygon")
            _label(polygon["label"], rules)
            points = polygon["points"]
            if not isinstance(points, list) or len(points) < 3 or len(points) > 2048:
                raise ValueError("polygon_requires_three_or_more_points")
            normalized_points = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError("invalid_polygon_point")
                normalized_points.append(Point(
                    _number(point[0], "polygon_x", minimum=0, maximum=1),
                    _number(point[1], "polygon_y", minimum=0, maximum=1),
                ))
            validate_polygon(normalized_points)
    elif task_type == "video_tracking":
        _keys(data, {"tracks"}, set(), "video_tracking")
        tracks = data["tracks"]
        if not isinstance(tracks, list) or not tracks or len(tracks) > 5000:
            raise ValueError("invalid_video_tracks")
        for track in tracks:
            item = _object(track, "track")
            _keys(item, {"track_id", "label", "frames"}, set(), "track")
            _string(item["track_id"], "track_id", maximum=128)
            _label(item["label"], rules)
            frames = item["frames"]
            if not isinstance(frames, list) or not frames:
                raise ValueError("track_requires_frames")
            previous = -1
            for frame in frames:
                box = _object(frame, "track_frame")
                _keys(box, {"frame", "x", "y", "width", "height"}, set(), "track_frame")
                number = box["frame"]
                if type(number) is not int or number < 0 or number <= previous:
                    raise ValueError("track_frames_must_be_strictly_increasing")
                previous = number
                _bbox({"label": item["label"], **{k: box[k] for k in ("x", "y", "width", "height")}}, rules)
    elif task_type in {"audio_transcription", "speaker_diarization"}:
        _keys(data, {"segments"}, set(), "audio_annotation")
        _segments(data["segments"], speaker_required=task_type == "speaker_diarization")
    elif task_type == "entity_extraction":
        _keys(data, {"entities"}, set(), "entity_extraction")
        entities = data["entities"]
        if not isinstance(entities, list) or len(entities) > 10000:
            raise ValueError("invalid_entities")
        source = rules.get("source_text")
        if source is not None and not isinstance(source, str):
            raise ValueError("invalid_rubric_source_text")
        previous_end = -1
        for entity in entities:
            item = _object(entity, "entity")
            _keys(item, {"text", "label", "start", "end"}, set(), "entity")
            text = _string(item["text"], "entity_text", maximum=12000)
            _string(item["label"], "entity_label", maximum=256)
            start, end = item["start"], item["end"]
            if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                raise ValueError("invalid_entity_offsets")
            if source is not None and (end > len(source) or source[start:end] != text):
                raise ValueError("entity_offsets_do_not_match_source")
            if start < previous_end:
                raise ValueError("entity_spans_must_be_ordered_and_non_overlapping")
            previous_end = end
    elif task_type == "response_ranking":
        _keys(data, {"ranking"}, {"rationale"}, "response_ranking")
        ranking = data["ranking"]
        if not isinstance(ranking, list) or len(ranking) < 2 or len(ranking) > 1000:
            raise ValueError("ranking_requires_at_least_two_responses")
        ids, ranks = set(), set()
        for item in ranking:
            row = _object(item, "ranking_item")
            _keys(row, {"response_id", "rank"}, set(), "ranking_item")
            response_id = _string(row["response_id"], "response_id", maximum=200)
            rank = row["rank"]
            if type(rank) is not int or rank < 1:
                raise ValueError("invalid_response_rank")
            if response_id in ids or rank in ranks:
                raise ValueError("response_ids_and_ranks_must_be_unique")
            ids.add(response_id)
            ranks.add(rank)
        if ranks != set(range(1, len(ranking) + 1)):
            raise ValueError("response_ranks_must_be_contiguous")
        if "rationale" in data:
            _string(data["rationale"], "rationale")
    elif task_type == "llm_evaluation":
        _keys(data, {"scores"}, {"rationale"}, "llm_evaluation")
        scores = _object(data["scores"], "evaluation_scores")
        if not scores or len(scores) > 100:
            raise ValueError("evaluation_scores_required")
        for criterion, score in scores.items():
            _string(criterion, "evaluation_criterion", maximum=128)
            _number(score, "evaluation_score", minimum=0, maximum=5)
        if "rationale" in data:
            _string(data["rationale"], "rationale")
    else:
        raise ValueError("unsupported_annotation_task_type")
