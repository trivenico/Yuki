import unittest

from core.annotation_geometry import Point
from core.annotation_interaction import AnnotationInteraction


class AnnotationInteractionTests(unittest.TestCase):
    def setUp(self):
        self.editor = AnnotationInteraction(1000, 500)

    def test_box_pointer_lifecycle_and_document_history(self):
        self.editor.pointer_down(7, Point(.8, .8))
        self.editor.pointer_move(7, Point(.2, .2))
        self.assertEqual(self.editor.drag_preview, (Point(.8, .8), Point(.2, .2)))
        rect = self.editor.pointer_up(7, Point(.2, .2), label="car")
        self.assertAlmostEqual(rect.x, .2)
        self.assertEqual(self.editor.document["boxes"][0]["label"], "car")
        self.assertTrue(self.editor.can_undo)
        self.editor.undo()
        self.assertEqual(self.editor.document["boxes"], [])
        self.editor.redo()
        self.assertEqual(len(self.editor.document["boxes"]), 1)

    def test_pointer_identity_and_gesture_exclusivity(self):
        self.editor.pointer_down("mouse-1", Point(.1, .1))
        with self.assertRaisesRegex(ValueError, "identity"):
            self.editor.pointer_move("mouse-2", Point(.2, .2))
        with self.assertRaisesRegex(ValueError, "active"):
            self.editor.pointer_down("mouse-2", Point(.2, .2))
        self.editor.cancel_gesture()
        self.assertFalse(self.editor.pointer_active)

    def test_failed_box_gesture_releases_pointer_without_mutating_document(self):
        self.editor.pointer_down(1, Point(.1, .1))
        with self.assertRaisesRegex(ValueError, "too_small"):
            self.editor.pointer_up(1, Point(.1001, .1001), label="car")
        self.assertFalse(self.editor.pointer_active)
        self.assertEqual(self.editor.document["boxes"], [])

    def test_polygon_vertices_validate_before_commit(self):
        self.editor.set_tool("polygon")
        for point in (Point(0, 0), Point(1, 1), Point(0, 1), Point(1, 0)):
            self.editor.add_polygon_vertex(point)
        with self.assertRaisesRegex(ValueError, "self_intersecting"):
            self.editor.finish_polygon(label="road")
        self.assertEqual(self.editor.document["polygons"], [])
        self.editor.cancel_gesture()
        for point in (Point(.1, .1), Point(.9, .1), Point(.9, .9), Point(.1, .9)):
            self.editor.add_polygon_vertex(point)
        self.editor.finish_polygon(label="road")
        self.assertEqual(len(self.editor.document["polygons"]), 1)

    def test_tool_switch_clears_uncommitted_polygon(self):
        self.editor.set_tool("polygon")
        self.editor.add_polygon_vertex(Point(.2, .2))
        self.editor.set_tool("box")
        self.assertEqual(self.editor.polygon_draft, ())

    def test_document_is_defensively_copied(self):
        self.editor.pointer_down(1, Point(.1, .1))
        self.editor.pointer_up(1, Point(.5, .5), label="object")
        exposed = self.editor.document
        exposed["boxes"].clear()
        self.assertEqual(len(self.editor.document["boxes"]), 1)


if __name__ == "__main__":
    unittest.main()
