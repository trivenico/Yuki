"""Bounded, offline document extraction adapters for Yuki knowledge ingestion."""
from __future__ import annotations

import hashlib
import html
import io
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


@dataclass(frozen=True)
class ParsedDocument:
    source_name: str
    source_path: str
    source_sha256: str
    text: str
    format: str
    page_count: int | None = None


class _TextHTMLParser(HTMLParser):
    BLOCKED = {"script", "style", "noscript", "svg", "canvas", "template"}
    BLOCKS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3",
              "h4", "h5", "h6", "section", "article", "header", "footer"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCKED:
            self._blocked += 1
        elif not self._blocked and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCKED and self._blocked:
            self._blocked -= 1
        elif not self._blocked and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._blocked:
            self.parts.append(data)


class DocumentParser:
    MAX_FILE_BYTES = 20_000_000
    MAX_EXTRACTED_CHARS = 200_000
    MAX_DOCX_ENTRIES = 2_000
    MAX_DOCX_EXPANDED_BYTES = 40_000_000
    SUPPORTED = {".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml",
                 ".html", ".htm", ".pdf", ".docx"}

    def parse_file(self, path: str | Path, *, workspace: str | Path) -> ParsedDocument:
        _, source = self._safe_file(path, workspace)
        raw = source.read_bytes()
        suffix = source.suffix.lower()
        if suffix in {".txt", ".md", ".rst", ".py", ".json", ".yaml", ".yml"}:
            extracted = self._decode(raw)
        elif suffix in {".html", ".htm"}:
            extracted = self._html(raw)
        elif suffix == ".docx":
            extracted = self._docx(raw)
        elif suffix == ".pdf":
            extracted, _pages = self._pdf(raw)
        else:
            raise ValueError(f"Unsupported document format: {suffix}")
        extracted = self._clean(extracted)
        if not extracted:
            raise ValueError("Document contains no extractable text.")
        if len(extracted) > self.MAX_EXTRACTED_CHARS:
            raise ValueError("Extracted document exceeds the 200,000 character limit.")
        return ParsedDocument(
            source_name=source.name, source_path=str(source),
            source_sha256=hashlib.sha256(raw).hexdigest(),
            text=extracted, format=suffix.lstrip("."),
            page_count=_pages if suffix == ".pdf" else None,
        )

    @classmethod
    def _safe_file(cls, path, workspace):
        workspace_path = Path(workspace).expanduser()
        workspace_lexical = workspace_path.absolute()
        root = workspace_path.resolve(strict=True)
        supplied = Path(path).expanduser()
        candidate = supplied if supplied.is_absolute() else workspace_lexical / supplied
        try:
            relative = candidate.absolute().relative_to(workspace_lexical)
        except ValueError as exc:
            raise ValueError("Document must be inside the approved workspace.") from exc
        candidate = root / relative
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("Symbolic links are not permitted as document sources.")
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents or not resolved.is_file():
            raise ValueError("Document must be a regular file inside the workspace.")
        if resolved.suffix.lower() not in cls.SUPPORTED:
            raise ValueError(f"Unsupported document format: {resolved.suffix.lower()}")
        if resolved.stat().st_size > cls.MAX_FILE_BYTES:
            raise ValueError("Document exceeds the 20 MB file limit.")
        return root, resolved

    @staticmethod
    def _decode(raw):
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text document must be valid UTF-8.") from exc

    @classmethod
    def _html(cls, raw):
        parser = _TextHTMLParser()
        parser.feed(cls._decode(raw))
        parser.close()
        return "".join(parser.parts)

    @classmethod
    def _docx(cls, raw):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > cls.MAX_DOCX_ENTRIES:
                    raise ValueError("DOCX archive contains too many entries.")
                expanded = sum(item.file_size for item in infos)
                if expanded > cls.MAX_DOCX_EXPANDED_BYTES:
                    raise ValueError("DOCX expanded content exceeds the 40 MB limit.")
                names = {item.filename for item in infos}
                if "word/document.xml" not in names:
                    raise ValueError("DOCX archive is missing its main document.")
                xml = archive.read("word/document.xml")
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            raise ValueError("Invalid DOCX document.") from exc
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise ValueError("DOCX document XML is malformed.") from exc
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", ns):
            value = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
            if value.strip():
                paragraphs.append(value)
        return "\n".join(paragraphs)

    @classmethod
    def _pdf(cls, raw):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF extraction requires optional dependency pypdf.") from exc
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported.")
            if len(reader.pages) > 500:
                raise ValueError("PDF exceeds the 500 page limit.")
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
                if sum(map(len, parts)) > cls.MAX_EXTRACTED_CHARS:
                    raise ValueError("Extracted PDF exceeds the text limit.")
            return "\n".join(parts), len(reader.pages)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Unable to safely parse this PDF.") from exc

    @staticmethod
    def _clean(text):
        text = html.unescape(text).replace("\x00", "")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
