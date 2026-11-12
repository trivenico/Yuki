import unittest

from core.annotation_geometry import Point
from core.annotation_viewport import AnnotationViewport


class AnnotationViewportTests(unittest.TestCase):
    def setUp(self):
        self.camera = AnnotationViewport.fit(1600, 900, 1000, 800)

    def test_fit_preserves_aspect_and_centers_image(self):
        self.assertAlmostEqual(self.camera.scale, 1000 / 1600)
        self.assertAlmostEqual(self.camera.offset_x, 0)
        self.assertAlmostEqual(self.camera.offset_y, 118.75)
        self.assertEqual(self.camera.zoom, 1)

    def test_zoom_at_keeps_source_point_under_cursor(self):
        anchor = Point(420, 360)
        before = self.camera.viewport_to_image(anchor)
        zoomed = self.camera.zoom_at(anchor, 2.5)
        after = zoomed.viewport_to_image(anchor)
        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertAlmostEqual(before.x, after.x)
        self.assertAlmostEqual(before.y, after.y)
        self.assertAlmostEqual(zoomed.zoom, 2.5)

    def test_zoom_limits_are_enforced_without_mutating_previous_camera(self):
        maximum = self.camera.zoom_at(Point(500, 400), 1e100)
        minimum = maximum.zoom_at(Point(500, 400), 1e-100)
        self.assertEqual(maximum.zoom, 16)
        self.assertEqual(minimum.zoom, .25)
        self.assertEqual(self.camera.zoom, 1)

    def test_pan_keeps_a_visible_image_margin(self):
        moved = self.camera.pan_by(-1e6, 1e6)
        bounds = moved.image_bounds
        self.assertGreaterEqual(bounds.x + bounds.width, 32)
        self.assertLessEqual(bounds.x, moved.viewport_width - 32)
        self.assertGreaterEqual(bounds.y + bounds.height, 32)
        self.assertLessEqual(bounds.y, moved.viewport_height - 32)

    def test_pan_rejects_nonfinite_and_boolean_deltas(self):
        for values in ((float("nan"), 0), (0, float("inf")), (True, 0)):
            with self.assertRaisesRegex(ValueError, "pan_delta"):
                self.camera.pan_by(*values)

    def test_zoom_rejects_invalid_factors_and_points(self):
        for factor in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaisesRegex(ValueError, "zoom_factor"):
                self.camera.zoom_at(Point(100, 100), factor)
        with self.assertRaisesRegex(ValueError, "invalid_point"):
            self.camera.zoom_at(Point(float("nan"), 0), 2)

    def test_normalized_geometry_round_trips_after_zoom_and_pan(self):
        camera = self.camera.zoom_at(Point(500, 400), 3).pan_by(-80, 25)
        point = Point(.27, .73)
        viewport = camera.image_to_viewport(point)
        result = camera.viewport_to_image(viewport)
        self.assertAlmostEqual(result.x, point.x)
        self.assertAlmostEqual(result.y, point.y)

    def test_reset_returns_to_fit(self):
        changed = self.camera.zoom_at(Point(500, 400), 4).pan_by(100, -50)
        reset = changed.reset()
        self.assertAlmostEqual(reset.zoom, 1)
        self.assertAlmostEqual(reset.offset_x, self.camera.offset_x)
        self.assertAlmostEqual(reset.offset_y, self.camera.offset_y)

    def test_invalid_fit_configuration_is_rejected(self):
        invalid = (
            (0, 100, 100, 100),
            (100, 100, float("inf"), 100),
        )
        for args in invalid:
            with self.assertRaises(ValueError):
                AnnotationViewport.fit(*args)
        with self.assertRaisesRegex(ValueError, "zoom_limits"):
            AnnotationViewport.fit(100, 100, 100, 100, min_zoom=2, max_zoom=4)


if __name__ == "__main__":
    unittest.main()
