"""Extract every PDF page into searchable Markdown and optional lossless-text JSON.

Requires pypdf (available in the application environment). This is text extraction,
not OCR: image-only pages and failed pages are explicitly reported. Raw page text
is retained in JSON so normalization never becomes the only research reference.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


def normalize_text(text: str) -> str:
    """Join prose wraps conservatively; preserve columns, formulae and headings."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00ad", "")
    # Some equation fonts extract delimiters as control characters. Keep the
    # raw JSON unchanged, but make Markdown searchable by ordinary text tools.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "\ufffd", text)
    text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", text)
    lines = [line.rstrip() for line in text.splitlines()]
    output: list[str] = []
    for line in lines:
        previous = output[-1] if output else ""
        prose = (
            previous and line and len(previous) > 55
            and re.search(r"[a-zA-Z,;]$", previous)
            and re.match(r"^[a-z]", line)
            and not re.search(r"\s{3,}|[=∑∏∫]", previous + line)
        )
        if prose:
            output[-1] = previous + " " + line
        else:
            output.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def extract_pdf(pdf_path: Path, *, minimum_characters: int = 80,
                strip_repeated_margins: bool = False) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        error = None
        try:
            raw = page.extract_text() or ""
        except Exception as exc:
            raw = ""
            error = f"{type(exc).__name__}: {exc}"
        characters = len(re.sub(r"\s", "", raw))
        warnings = []
        if error:
            warnings.append(f"Extraction failed: {error}")
        if characters < minimum_characters:
            warnings.append(f"Only {characters} non-whitespace characters; inspect page visually or use OCR.")
        pages.append({"page": number, "raw_text": raw, "text": raw,
                      "characters": characters, "warnings": warnings, "removed_margins": []})

    # Opt-in and deliberately conservative: only identical first/last text lines
    # on >= 60% of pages. Preserve the original extraction in raw_text regardless.
    if strip_repeated_margins and len(pages) >= 3:
        counts: Counter = Counter()
        for page in pages:
            lines = [s.strip() for s in page["raw_text"].splitlines() if s.strip()]
            if lines:
                counts.update(set((lines[0], lines[-1])))
        repeated = {s for s, n in counts.items() if n >= max(3, len(pages) * 0.6)}
        for page in pages:
            lines = page["text"].splitlines()
            occupied = [i for i, line in enumerate(lines) if line.strip()]
            if occupied:
                for i in set((occupied[0], occupied[-1])):
                    if lines[i].strip() in repeated:
                        page["removed_margins"].append(lines[i])
                        lines[i] = ""
            page["text"] = "\n".join(lines)
    for page in pages:
        page["text"] = normalize_text(page["text"])
    return {
        "source": str(pdf_path.resolve()), "page_count": len(pages),
        "method": "pypdf text extraction; no OCR; raw text retained per page",
        "limitations": "Reading order, mathematical symbols and figure contents may require visual verification.",
        "pages": pages,
    }


def render_markdown(document: dict) -> str:
    blocks = [f"# {Path(document['source']).stem}\n\n"
              f"Source: `{document['source']}`\n\n"
              f"Pages: {document['page_count']}\n\n"
              f"{document['method']}. {document['limitations']}\n"]
    for page in document["pages"]:
        blocks.append(f"\n<!-- PAGE {page['page']} -->\n\n## Page {page['page']}\n")
        for warning in page["warnings"]:
            blocks.append(f"\n> Extraction warning: {warning}\n")
        blocks.append("\n" + (page["text"] or "[No extractable text]") + "\n")
    return "".join(blocks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", dest="json_output", type=Path)
    parser.add_argument("--minimum-characters", type=int, default=80)
    parser.add_argument("--strip-repeated-margins", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = args.output or root / "logs" / "research" / "extracted" / (args.pdf.stem + ".md")
    targets = [output] + ([args.json_output] if args.json_output else [])
    if len({p.resolve() for p in targets + [args.pdf]}) != len(targets) + 1:
        parser.error("Input PDF, Markdown output and JSON output must have different paths.")
    for path in targets:
        if path.exists() and not args.overwrite:
            parser.error(f"Output exists: {path}. Use --overwrite to replace it.")
    try:
        document = extract_pdf(args.pdf, minimum_characters=args.minimum_characters,
                               strip_repeated_margins=args.strip_repeated_margins)
    except (ImportError, OSError, ValueError) as exc:
        parser.exit(2, f"PDF extraction failed: {exc}\nUse an environment with pypdf installed.\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(document), encoding="utf-8")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = 0
    for page in document["pages"]:
        for warning in page["warnings"]:
            print(f"Page {page['page']}: {warning}", file=sys.stderr)
            failures += 1
    print(f"Extracted {document['page_count']} pages to {output}; {failures} warnings.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
