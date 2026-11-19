from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebDeploymentImageTests(unittest.TestCase):
    def test_web_image_is_separate_from_cli_image(self):
        web_image = (ROOT / "docker" / "Dockerfile.web").read_text(encoding="utf-8")
        cli_image = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "scripts/run_web.py"]', web_image)
        self.assertIn("requirements-web.txt", web_image)
        self.assertIn("USER 10001:10001", web_image)
        self.assertIn("YUKI_WORKSPACE=/workspace", web_image)
        self.assertIn('CMD ["python", "main.py"]', cli_image)
        self.assertNotIn("scripts/run_web.py", cli_image)

    def test_web_image_documents_private_proxy_and_persistent_volume(self):
        web_image = (ROOT / "docker" / "Dockerfile.web").read_text(encoding="utf-8")
        self.assertIn("persistent, service-owned volume", web_image)
        self.assertIn("TLS reverse proxy", web_image)


if __name__ == "__main__":
    unittest.main()
