"""Isolated PDF parser. Only the parent performs I/O with external sources."""

import json
import sys


def parse_pdf(data):
    import pymupdf as fitz

    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                return {"error": "evidence_pdf_encrypted"}
            if document.page_count > 200:
                return {"error": "evidence_pdf_page_limit"}
            chunks, size = [], 0
            # 200 pages are bounded independently of the retained output text.
            for page in document:
                text = page.get_text()
                if text.strip():
                    chunks.append(text)
                    size += len(text)
                if size > 2 * 1024 * 1024:
                    break
            if not chunks:
                return {"error": "evidence_pdf_scan_no_text"}
            from .passages import select_passages

            return {
                "text": select_passages(" ".join(chunks), limit=12000),
                "title": (document.metadata or {}).get("title") or "Öffentlicher PDF-Beleg",
            }
    except Exception:
        # PDF exception text can contain document data. Expose a safe code only.
        return {"error": "evidence_pdf_invalid"}


if __name__ == "__main__":
    payload = sys.stdin.buffer.read(15 * 1024 * 1024 + 1)
    result = {"error": "evidence_size_limit"} if len(payload) > 15 * 1024 * 1024 else parse_pdf(payload)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
