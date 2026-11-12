import unittest
from io import BytesIO

from PIL import Image

from core.annotation_image_preview import (
    AnnotationImagePreviewError,
    create_safe_png_preview,
)


def _image_bytes(image_format="PNG", size=(4, 3), frames=1):
    output = BytesIO()
    images = [Image.new("RGB", size, (index, 20, 30)) for index in range(frames)]
    images[0].save(
        output, format=image_format,
        save_all=frames > 1,
        append_images=images[1:],
        duration=100,
        loop=0,
    )
    for image in images:
        image.close()
    return output.getvalue()


class AnnotationImagePreviewTests(unittest.TestCase):
    def test_jpeg_is_decoded_and_reencoded_as_metadata_free_png(self):
        preview = create_safe_png_preview(_image_bytes("JPEG"))
        with Image.open(BytesIO(preview)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (4, 3))
            self.assertFalse(image.info)

    def test_animated_gif_is_rejected(self):
        with self.assertRaises(AnnotationImagePreviewError):
            create_safe_png_preview(_image_bytes("GIF", frames=2))

    def test_non_image_and_malformed_content_are_rejected(self):
        for data in (b"not an image", b""):
            with self.subTest(data=data):
                with self.assertRaises(AnnotationImagePreviewError):
                    create_safe_png_preview(data)

    def test_dimension_limit_is_checked_before_decode(self):
        with self.assertRaises(AnnotationImagePreviewError):
            create_safe_png_preview(_image_bytes("PNG", size=(12001, 1)))


if __name__ == "__main__":
    unittest.main()
