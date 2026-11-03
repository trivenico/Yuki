import tempfile
import unittest
import zipfile
from pathlib import Path

from knowledge.document_parsers import DocumentParser
from knowledge.engine import KnowledgeEngine
from knowledge.vector_store import VectorStore
from knowledge.indexing_coordinator import SemanticIndexCoordinator


class FakeEmbedder:
    model_id = "test/documents"
    def embed(self, texts):
        return [[float(len(text) % 7 + 1), 1.0, 0.5] for text in texts]


class DocumentParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.parser = DocumentParser()

    def tearDown(self):
        self.temp.cleanup()

    def test_extracts_html_and_ignores_script_and_style(self):
        path = self.workspace / "page.html"
        path.write_text(
            "<html><head><style>secret_style</style></head><body>"
            "<h1>Yuki research</h1><script>secret_script()</script>"
            "<p>Local intelligence platform</p></body></html>",
            encoding="utf-8",
        )
        doc = self.parser.parse_file(path, workspace=self.workspace)
        self.assertIn("Yuki research", doc.text)
        self.assertIn("Local intelligence platform", doc.text)
        self.assertNotIn("secret_script", doc.text)
        self.assertNotIn("secret_style", doc.text)
        self.assertEqual(doc.format, "html")

    def test_extracts_docx_main_document_text(self):
        path = self.workspace / "research.docx"
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Yuki document intelligence</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p></w:body></w:document>'
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", xml)
        doc = self.parser.parse_file(path, workspace=self.workspace)
        self.assertIn("Yuki document intelligence", doc.text)
        self.assertIn("Second paragraph", doc.text)
        self.assertEqual(doc.format, "docx")

    def test_rejects_invalid_docx_and_workspace_escape(self):
        bad = self.workspace / "bad.docx"
        bad.write_bytes(b"not a zip file")
        with self.assertRaises(ValueError):
            self.parser.parse_file(bad, workspace=self.workspace)
        outside = self.root / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.parser.parse_file(outside, workspace=self.workspace)

    def test_rejects_oversized_docx_archive(self):
        path = self.workspace / "large.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", "x" * (self.parser.MAX_DOCX_EXPANDED_BYTES + 1))
        with self.assertRaises(ValueError):
            self.parser.parse_file(path, workspace=self.workspace)


class DocumentCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.engine = KnowledgeEngine(self.root / "knowledge.sqlite3")
        self.vectors = VectorStore(self.root / "vectors.sqlite3")
        self.coordinator = SemanticIndexCoordinator(
            self.engine, self.vectors, FakeEmbedder()
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_indexes_html_and_docx_through_same_pipeline(self):
        html_file = self.workspace / "guide.html"
        html_file.write_text("<h1>Yuki knowledge</h1><p>Local search</p>", encoding="utf-8")
        result = self.coordinator.index_file(html_file, workspace=self.workspace)
        self.assertEqual(result["document_format"], "html")
        self.assertGreater(result["vectors_indexed"], 0)
        docx_file = self.workspace / "guide.docx"
        with zipfile.ZipFile(docx_file, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>DOCX autonomous intelligence</w:t></w:r></w:p>'
                '</w:body></w:document>',
            )
        second = self.coordinator.index_file(docx_file, workspace=self.workspace)
        self.assertEqual(second["document_format"], "docx")
        self.assertGreater(second["vectors_indexed"], 0)

    def test_detects_source_changed_between_parse_and_ingestion(self):
        source = self.workspace / "notes.md"
        source.write_text("original content", encoding="utf-8")
        parsed = self.coordinator.parser.parse_file(source, workspace=self.workspace)
        source.write_text("modified after parse", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            self.engine.ingest_extracted_text(
                source_path=parsed.source_path, workspace=self.workspace,
                source_sha256=parsed.source_sha256, text=parsed.text,
            )


if __name__ == "__main__":
    unittest.main()
