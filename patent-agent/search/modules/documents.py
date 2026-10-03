"""Extract local patent PDF text with physical page coordinates and quality flags.

PDF text is untrusted evidence, never executable instructions.  Extraction does
not establish completeness or a patent match.  Completeness requires an explicit
human confirmation in the workflow, tied to this PDF's SHA-256 digest.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
from io import BytesIO
from pathlib import Path
import re
import unicodedata

from .normalize import parse_publication_number


_PUB_PATTERN = re.compile(r"(?<![A-Z0-9])([A-Z]{2})\s*([0-9](?:\s*[0-9]){4,13})\s*([A-Z][0-9]?)(?![A-Z0-9])")
_SECTIONS = {"权利要求书": "claims", "说明书附图": "drawings", "说明书": "description", "摘要": "abstract"}


def _publications(text):
    text = unicodedata.normalize("NFKC", text).upper()
    return list(dict.fromkeys("".join(re.sub(r"\s+", "", part) for part in match.groups())
                              for match in _PUB_PATTERN.finditer(text)))


def _header_publications(text):
    header = text[:1200]
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", header))
    explicit_header = bool(re.search(r"国家知识产权局|中华人民共和国|\((?:10|11)\).{0,20}(?:号|publication)", compact, re.IGNORECASE))
    first_lines = [line.strip() for line in header.splitlines() if line.strip()][:3]
    number_only_line = any(parse_publication_number(line) for line in first_lines)
    # A number mentioned in an ordinary narrative or a reference list is not
    # enough to associate the PDF with that publication.
    return _publications(header) if explicit_header or number_only_line else []


def _page_labels(text):
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    labels = []
    # Section counts on Chinese front sheets are NOT page labels.  A slash or
    # explicit 'of' is required before treating a number as a page coordinate.
    pattern = r"(权利要求书|说明书附图|说明书|摘要)(?:第)?(\d+)(?:/|共)(\d+)页"
    for match in re.finditer(pattern, compact):
        labels.append({"section": _SECTIONS[match.group(1)], "number": int(match.group(2)), "total": int(match.group(3))})
    for match in re.finditer(r"Page\s+(\d+)\s+of\s+(\d+)", text, re.IGNORECASE):
        labels.append({"section": "document", "number": int(match.group(1)), "total": int(match.group(2))})
    return labels


def _claims(pages):
    # These are extraction candidates.  Independent status is deliberately not
    # inferred from silence: the reading Skill must review the actual language.
    text = "\n".join(page["text"] for page in pages if page.get("section") == "claims")
    starts = list(re.finditer(r"(?m)^\s*(\d{1,3})[.．、]\s*", text))
    claims = []
    for index, marker in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        content = text[marker.start():end].strip()
        dependent = bool(re.search(r"(?:根据|如|按照)权利要求\s*\d", content))
        claims.append({"no": int(marker.group(1)), "independent": False if dependent else None, "text": content})
    return claims


def extract_pdf(path: Path, expected_key: str | None = None) -> dict:
    """Return per-page text and conservative quality metadata without side effects.

    ``identity_verified`` requires one unambiguous full publication number in the
    first-page header area; a matching filename or a citation in the body alone
    cannot verify identity.  ``complete`` is always False at this stage.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到 PDF：{path}")
    if expected_key is not None:
        parsed = parse_publication_number(expected_key)
        if parsed is None:
            raise ValueError("PDF 关联必须使用包含公布机构、号码、种类码的完整公开号。")
        expected_key = "".join(parsed)
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("提取 PDF 需要 pypdf，请按 requirements.txt 安装依赖。") from exc
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    pages = []
    issues = ["completeness_requires_human_confirmation"]
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，无法提取；请提供可读取的 PDF。")
        for index, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                text = ""
                issues.append(f"page_extraction_failed:{index}:{type(exc).__name__}")
            item = {"page": index, "text": text}
            labels = _page_labels(text)
            if labels:
                item["labels"] = labels
                section_labels = [label for label in labels if label["section"] != "document"]
                if section_labels:
                    item["section"] = section_labels[0]["section"]
            if "section" not in item:
                header = re.sub(r"\s+", "", text[:500])
                for label, section in _SECTIONS.items():
                    if label in header:
                        item["section"] = section
                        break
            pages.append(item)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"无法读取 PDF（{type(exc).__name__}）：{path.name}") from exc
    if not pages:
        issues.append("empty_pdf")
    # Patent front-page headers identify the publication.  Search a bounded
    # header, not a full-body reference list; competing numbers remain unknown.
    header_numbers = _header_publications(pages[0]["text"]) if pages else []
    filename_numbers = _publications(path.stem)
    body_key = header_numbers[0] if len(header_numbers) == 1 else None
    key = expected_key or body_key or (filename_numbers[0] if len(filename_numbers) == 1 else None)
    identity_verified = bool(body_key and body_key == key)
    if not identity_verified:
        issues.append("publication_identity_unverified")
    if expected_key and header_numbers and expected_key not in header_numbers:
        issues.append("publication_number_mismatch")
    if len(header_numbers) > 1:
        issues.append("ambiguous_publication_header")
    text_pages = [page["page"] for page in pages if re.search(r"[\w\u3400-\u9fff]", page["text"])]
    no_text_pages = [page["page"] for page in pages if page["page"] not in text_pages]
    if no_text_pages:
        issues.append("pages_without_extractable_text:" + ",".join(map(str, no_text_pages)))
    counters = defaultdict(list)
    section_pages = defaultdict(list)
    for page in pages:
        if page.get("section"):
            section_pages[page["section"]].append(page["page"])
        for label in page.get("labels", []):
            counters[label["section"]].append(label)
    missing_pages = False
    for section, labels in counters.items():
        totals = {label["total"] for label in labels}
        observed = {label["number"] for label in labels}
        if len(totals) != 1 or any(label["number"] < 1 or label["number"] > label["total"] for label in labels):
            missing_pages = True
            issues.append(f"inconsistent_page_labels:{section}")
        elif observed != set(range(1, next(iter(totals)) + 1)):
            missing_pages = True
            issues.append(f"missing_document_pages:{section}")
        if len(observed) != len(labels):
            missing_pages = True
            issues.append(f"duplicate_page_labels:{section}")
    return {"key": key, "sha256": digest, "path": str(path.resolve()), "pages": pages,
            "quality": {"text_available": bool(text_pages), "needs_ocr": bool(no_text_pages),
                        "identity_verified": identity_verified, "complete": False,
                        "missing_pages_detected": missing_pages, "text_pages": text_pages,
                        "pages_without_text": no_text_pages, "header_publication_numbers": header_numbers,
                        "issues": issues},
            "claims": _claims(pages),
            "sections": [{"name": name, "pages": positions} for name, positions in section_pages.items()]}
