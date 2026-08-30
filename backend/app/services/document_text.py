"""Bounded, best-effort text extraction for server-side document analysis."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from app.core.config import settings

DOCUMENT_TEXT_REVISION = "bounded-structured-v1"

DOCUMENT_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".rtf",
        ".odt",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }
)


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag.casefold() in {"script", "style", "noscript", "svg", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if (
            tag.casefold() in {"script", "style", "noscript", "svg", "template"}
            and self._ignored_depth
        ):
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.values.append(data.strip())


def _bounded_join(values, max_chars: int) -> str:  # noqa: ANN001
    output: list[str] = []
    used = 0
    for raw in values:
        value = " ".join(str(raw or "").replace("\x00", " ").split())
        if not value:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        value = value[:remaining]
        output.append(value)
        used += len(value) + 1
    return "\n".join(output)[:max_chars]


def extract_html_text(raw: str, *, max_chars: int) -> str:
    parser = _VisibleHTML()
    try:
        parser.feed(raw[: max_chars * 4])
    except Exception:
        # Malformed HTML still benefits from a conservative tag strip.
        return _bounded_join(
            [html.unescape(re.sub(r"<[^>]{0,2048}>", " ", raw))],
            max_chars,
        )
    return _bounded_join(parser.values, max_chars)


def extract_json_text(raw: str, *, max_chars: int) -> str:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""

    values: list[str] = []
    remaining_nodes = 20_000

    def visit(value) -> None:  # noqa: ANN001
        nonlocal remaining_nodes
        if remaining_nodes <= 0:
            return
        remaining_nodes -= 1
        if isinstance(value, str):
            cleaned = value.strip()
            # Ignore likely base64/blob fields. They add noise and may contain
            # accidental risk tokens while carrying no human-readable context.
            if cleaned and not (
                len(cleaned) > 512
                and re.fullmatch(r"[A-Za-z0-9+/=_-]+", cleaned) is not None
            ):
                values.append(cleaned)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return _bounded_join(values, max_chars)


def _xml_text(raw: bytes, max_chars: int) -> str:
    try:
        root = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, ValueError):
        return ""
    return _bounded_join(root.itertext(), max_chars)


def _zip_xml_text(path: Path, ext: str, max_chars: int) -> str:
    selectors = {
        ".docx": lambda name: (
            name == "word/document.xml"
            or name.startswith("word/header")
            or name.startswith("word/footer")
            or name in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
        ),
        ".pptx": lambda name: name.startswith("ppt/slides/slide") and name.endswith(".xml"),
        ".xlsx": lambda name: (
            name == "xl/sharedStrings.xml"
            or (name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        ),
        ".odt": lambda name: name == "content.xml",
    }
    selector = selectors.get(ext)
    if selector is None:
        return ""
    output: list[str] = []
    used = 0
    expanded = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 4096:
                return ""
            for info in infos:
                if not selector(info.filename):
                    continue
                expanded += max(0, int(info.file_size))
                if expanded > settings.document_extract_max_bytes * 4:
                    break
                raw = archive.read(info)
                value = _xml_text(raw, max_chars - used)
                if value:
                    output.append(value)
                    used += len(value) + 1
                if used >= max_chars:
                    break
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        return ""
    return "\n".join(output)[:max_chars]


def _command_text(command: list[str], max_chars: int, timeout_s: int = 60) -> str:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="ignore")[:max_chars]


def _pdf_text(path: Path, max_chars: int) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return ""
            except Exception:
                return ""
        values: list[str] = []
        used = 0
        for index, page in enumerate(reader.pages):
            if index >= 200:
                break
            try:
                value = page.extract_text() or ""
            except Exception:
                continue
            values.append(value)
            used += len(value)
            if used >= max_chars:
                break
        return _bounded_join(values, max_chars)
    except Exception:
        if not shutil.which("pdftotext"):
            return ""
        return _command_text(
            ["pdftotext", "-f", "1", "-l", "200", str(path), "-"],
            max_chars,
        )


def _rtf_text(raw: str, max_chars: int) -> str:
    value = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
    value = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", value)
    value = value.replace("{", " ").replace("}", " ")
    return _bounded_join([value], max_chars)


def extract_document_text(
    path: Path,
    mime: str = "",
    *,
    max_chars: int | None = None,
) -> str:
    """Extract bounded human-readable content without interpreting metadata keys."""
    limit = int(max_chars or settings.document_extract_max_chars)
    try:
        if not path.is_file() or path.stat().st_size > settings.document_extract_max_bytes:
            return ""
    except OSError:
        return ""
    ext = path.suffix.casefold()
    try:
        if ext == ".pdf" or mime == "application/pdf":
            return _pdf_text(path, limit)
        if ext in {".docx", ".pptx", ".xlsx", ".odt"}:
            return _zip_xml_text(path, ext, limit)
        if ext == ".doc" and shutil.which("antiword"):
            return _command_text(["antiword", str(path)], limit)
        if ext == ".ppt" and shutil.which("catppt"):
            return _command_text(["catppt", str(path)], limit)
        if ext == ".xls" and shutil.which("xls2csv"):
            return _command_text(["xls2csv", str(path)], limit)
        if ext == ".rtf":
            return _rtf_text(path.read_text(encoding="utf-8", errors="ignore"), limit)
    except (OSError, ValueError):
        return ""
    return ""
