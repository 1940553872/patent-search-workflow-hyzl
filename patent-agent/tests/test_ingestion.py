"""Formal ingestion regression cases; synthetic fixtures are not real patents."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from search.modules.retrieve import read_export
from search.modules.normalize import normalize_record, merge_records, parse_publication_number
from search.modules.documents import extract_pdf, _page_labels


def make_pdf(path, texts):
    """Create real PDF pages with ASCII text using the declared pypdf dependency."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=600, height=800)
        if not text:
            continue
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        commands = ["BT /F1 12 Tf 50 750 Td"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj 0 -18 Td")
        commands.append("ET")
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        # Python 3.12's Windows 0o700 temporary directories can exclude a sandbox
        # restricted token.  Default inherited workspace permissions work here.
        self.root = Path(__file__).resolve().parent / (".ingestion-" + uuid4().hex)
        self.root.mkdir()

    def tearDown(self):
        expected_parent = Path(__file__).resolve().parent
        if self.root.resolve().parent != expected_parent or not self.root.name.startswith(".ingestion-"):
            raise RuntimeError("临时目录范围异常，停止清理。")
        shutil.rmtree(self.root)


class ExportTests(WorkspaceCase):

    def test_csv_chinese_aliases_and_multiline(self):
        path = self.root / "records.csv"
        path.write_text('公开（公告）号,专利名称,申请日,摘要\nCN123456789A,测试,2024/1/2,"第一行\n第二行"\nCN123456788A,测试二,2024/1/2,摘要二\n', encoding="utf-8-sig", newline="")
        rows = read_export(path)
        record = normalize_record(rows[0], {"file": path.name, "row": rows[0]["_source_row"]})
        self.assertEqual(record["key"], "CN123456789A")
        self.assertEqual(record["dates"]["app"], "2024-01-02")
        self.assertEqual(record["abstract"], "第一行\n第二行")
        self.assertEqual(rows[0]["_source_row"], 2)
        self.assertEqual(rows[1]["_source_row"], 4)

    def test_gb18030_csv_and_zero_hits(self):
        path = self.root / "records.csv"
        path.write_bytes("公开号,名称\nCN123456789A,样例\n".encode("gb18030"))
        self.assertEqual(len(read_export(path)), 1)
        path.write_text("公开号,名称\n", encoding="utf-8")
        self.assertEqual(read_export(path), [])

    def test_duplicate_headers_and_extra_columns_rejected(self):
        path = self.root / "records.csv"
        for content in ("title,title\na,b\n", "title\na,b\n"):
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                read_export(path)

    def test_json_shape(self):
        path = self.root / "records.json"
        path.write_text(json.dumps({"records": [{"publication_number": "CN123456789B"}]}), encoding="utf-8")
        self.assertEqual(read_export(path)[0]["_source_row"], 1)
        path.write_text('{"records": ["bad"]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            read_export(path)

    def test_xlsx_all_sheets_dates_and_formula_no_execution(self):
        from openpyxl import Workbook
        path = self.root / "records.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "导出一"
        sheet.append(["公开号", "申请日", "标题"])
        sheet.append(["CN123456789A", date(2024, 1, 2), '=HYPERLINK("https://invalid.example", "x")'])
        other = book.create_sheet("导出二")
        other.append(["公开号", "名称"])
        other.append(["CN123456789B", "授权文本"])
        book.save(path)
        book.close()
        rows = read_export(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["_source_sheet"], "导出一")
        self.assertTrue(rows[0]["标题"].startswith("=HYPERLINK"))
        self.assertEqual(normalize_record(rows[0], {})["dates"]["app"], "2024-01-02")


class NormalizeTests(unittest.TestCase):
    def test_kind_codes_must_not_merge(self):
        a = normalize_record({"公开号": " CN 123456789 A ", "申请号": "CN202410001234.5"}, {"id": "a"})
        b = normalize_record({"公开号": "CN123456789B", "申请号": "CN202410001234.5"}, {"id": "b"})
        self.assertEqual(a["key"], "CN123456789A")
        with self.assertRaises(ValueError):
            merge_records(a, b)
        self.assertIsNone(parse_publication_number("CN202410001234.5"))

    def test_conflicts_keep_values_and_sources_and_never_guess(self):
        a = normalize_record({"公开号": "CN123456789A", "公开日": "2024-01-01"}, {"id": "a"})
        b = normalize_record({"公开号": "CN123456789A", "公开日": "2024-01-02"}, {"id": "b"})
        original = deepcopy(a)
        merged = merge_records(a, b)
        self.assertEqual(a, original)
        self.assertIsNone(merged["dates"]["pub"])
        self.assertEqual({item["value"] for item in merged["conflicts"]["dates.pub"]}, {"2024-01-01", "2024-01-02"})
        self.assertEqual(merged["conflicts"]["dates.pub"][1]["sources"], [{"id": "b"}])
        self.assertIsNone(merge_records(merged, a)["dates"]["pub"])

    def test_missing_can_fill_without_false_conflict(self):
        a = normalize_record({"公开号": "CN123456789A"}, {"id": "a"})
        b = normalize_record({"公开号": "CN123456789A", "标题": "标题"}, {"id": "b"})
        result = merge_records(a, b)
        self.assertEqual(result["title"], "标题")
        self.assertNotIn("title", result["conflicts"])
        self.assertNotIn("missing:title", result["issues"])

    def test_unknown_keys_are_stable_but_do_not_cross_source_merge(self):
        row = {"公开号": "123456789", "标题": "资料"}
        a = normalize_record(row, {"id": "a"})
        self.assertTrue(a["key"].startswith("UNKNOWN-"))
        self.assertEqual(a["key"], normalize_record(row, {"id": "a"})["key"])
        self.assertNotEqual(a["key"], normalize_record(row, {"id": "b"})["key"])

    def test_date_fields_not_substituted_and_partial_dates_unknown(self):
        row = normalize_record({"公开号": "CN123456789A", "公开日": "2024-06", "优先权日": "2023年2月1日", "申请日": "2023-02-30"}, {})
        self.assertIsNone(row["dates"]["pub"])
        self.assertIsNone(row["dates"]["app"])
        self.assertEqual(row["dates"]["pri"], "2023-02-01")

    def test_conflicting_aliases_do_not_select_first(self):
        record = normalize_record({"公开号": "CN123456789A", "publication_number": "CN123456789B"}, {})
        self.assertTrue(record["key"].startswith("UNKNOWN-"))
        self.assertEqual(len(record["conflicts"]["publication_number"]), 2)


class PdfTests(WorkspaceCase):
    def test_searchable_is_not_complete_and_hash_is_stable(self):
        path = self.root / "CN123456789A.pdf"
        make_pdf(path, ["CN123456789A\nA synthetic patent example.\nPage 1 of 1"])
        result = extract_pdf(path, "CN123456789A")
        self.assertTrue(result["quality"]["text_available"])
        self.assertTrue(result["quality"]["identity_verified"])
        self.assertFalse(result["quality"]["complete"])
        self.assertEqual(result["pages"][0]["page"], 1)
        self.assertEqual(result["sha256"], extract_pdf(path)["sha256"])

    def test_wrong_kind_and_filename_do_not_verify_identity(self):
        path = self.root / "CN123456789A.pdf"
        make_pdf(path, ["CN123456789B\nGranted text."])
        result = extract_pdf(path, "CN123456789A")
        self.assertFalse(result["quality"]["identity_verified"])
        self.assertIn("publication_number_mismatch", result["quality"]["issues"])

    def test_body_reference_does_not_verify_identity(self):
        path = self.root / "CN123456789A.pdf"
        make_pdf(path, ["Research article\nThis article cites CN123456789A as related work."])
        self.assertFalse(extract_pdf(path, "CN123456789A")["quality"]["identity_verified"])

    def test_blank_or_scanned_pages_are_pending_ocr(self):
        path = self.root / "scan.pdf"
        make_pdf(path, [None])
        result = extract_pdf(path, "CN123456789A")
        self.assertTrue(result["quality"]["needs_ocr"])
        self.assertFalse(result["quality"]["text_available"])
        self.assertFalse(result["quality"]["complete"])

    def test_missing_pages_detected_without_claiming_other_pdfs_complete(self):
        path = self.root / "partial.pdf"
        make_pdf(path, ["CN123456789A\nPage 1 of 3", "Page 3 of 3"])
        result = extract_pdf(path, "CN123456789A")
        self.assertTrue(result["quality"]["missing_pages_detected"])
        self.assertIn("missing_document_pages:document", result["quality"]["issues"])
        self.assertEqual(_page_labels("权 利 要 求 书 1/2 页"), [{"section": "claims", "number": 1, "total": 2}])


if __name__ == "__main__":
    unittest.main()
