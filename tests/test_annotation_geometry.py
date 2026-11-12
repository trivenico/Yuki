import unittest

from core.annotation_geometry import (
    Point, Rect, SnapshotHistory, ViewportTransform,
    bounding_box_from_drag, polygon_area, validate_polygon,
)


class ViewportTransformTests(unittest.TestCase):
    def test_fit_preserves_aspect_and_centers_letterbox(self):
        transform = ViewportTransform.fit(1600, 900, 1000, 1000)
        self.assertAlmostEqual(transform.scale, 0.625)
        self.assertAlmostEqual(transform.offset_x, 0)
        self.assertAlmostEqual(transform.offset_y, 218.75)
        self.assertEqual(transform.viewport_to_image(Point(500, 100)), None)
        self.assertEqual(transform.viewport_to_image(Point(500, 500)), Point(.5, .5))

    def test_round_trip_normalized_points(self):
        transform = ViewportTransform.fit(640, 480, 800, 600)
        point = Point(.25, .75)
        viewport = transform.image_to_viewport(point)
        self.assertAlmostEqual(transform.viewport_to_image(viewport).x, point.x)
        self.assertAlmostEqual(transform.viewport_to_image(viewport).y, point.y)

    def test_rejects_invalid_dimensions_and_nonfinite_points(self):
        for args in ((0, 10, 100, 100), (10, 10, float("inf"), 100)):
            with self.assertRaises(ValueError):
                ViewportTransform.fit(*args)
        with self.assertRaises(ValueError):
            ViewportTransform.fit(10, 10, 100, 100).viewport_to_image(
                Point(float("nan"), 0)
            )


class AnnotationGeometryTests(unittest.TestCase):
    def test_bounding_box_supports_reverse_drag(self):
        box = bounding_box_from_drag(
            Point(.8, .7), Point(.2, .1),
            image_width=1000, image_height=1000,
        )
        self.assertAlmostEqual(box.x, .2)
        self.assertAlmostEqual(box.y, .1)
        self.assertAlmostEqual(box.width, .6)
        self.assertAlmostEqual(box.height, .6)

    def test_rejects_tiny_or_out_of_bounds_boxes(self):
        with self.assertRaisesRegex(ValueError, "too_small"):
            bounding_box_from_drag(
                Point(.1, .1), Point(.1001, .2),
                image_width=100, image_height=100,
            )
        with self.assertRaises(ValueError):
            bounding_box_from_drag(
                Point(-.1, .1), Point(.5, .5),
                image_width=100, image_height=100,
            )

    def test_polygon_area_and_validation(self):
        points = [Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1)]
        self.assertAlmostEqual(polygon_area(points), 1)
        self.assertEqual(len(validate_polygon(points)), 4)

    def test_polygon_rejects_duplicate_degenerate_and_self_intersecting_vertices(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_polygon([Point(0, 0), Point(1, 0), Point(1, 0), Point(0, 1)])
        with self.assertRaisesRegex(ValueError, "area"):
            validate_polygon([Point(0, 0), Point(.5, .5), Point(1, 1)])
        with self.assertRaisesRegex(ValueError, "self_intersecting"):
            validate_polygon([Point(0, 0), Point(1, 1), Point(0, 1), Point(1, 0)])


class SnapshotHistoryTests(unittest.TestCase):
    def test_undo_redo_and_new_edit_clears_redo(self):
        history = SnapshotHistory({"boxes": []}, limit=2)
        history.push({"boxes": [1]})
        history.push({"boxes": [1, 2]})
        self.assertEqual(history.undo(), {"boxes": [1]})
        self.assertEqual(history.redo(), {"boxes": [1, 2]})
        history.undo()
        history.push({"boxes": [3]})
        self.assertFalse(history.can_redo)

    def test_snapshots_are_defensively_copied_and_bounded(self):
        original = {"points": [1]}
        history = SnapshotHistory(original, limit=1)
        original["points"].append(2)
        current = history.current
        current["points"].append(3)
        self.assertEqual(history.current, {"points": [1]})
        history.push({"points": [4]})
        history.push({"points": [5]})
        self.assertTrue(history.can_undo)
        self.assertEqual(history.undo(), {"points": [4]})
        self.assertEqual(history.current, {"points": [4]})


if __name__ == "__main__":
    unittest.main()
