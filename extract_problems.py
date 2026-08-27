"""Extract orange three-digit problems from a textbook PDF.

The detector uses the PDF's embedded text coordinates and color, so it does
not need OCR for digitally-created PDFs such as the supplied sample.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium


NUMBER_RE = re.compile(r"\d{3}")
TARGET_CMYK = (0.0, 0.6, 1.0, 0.0)


@dataclass(frozen=True)
class Marker:
    number: str
    x0: float
    top: float
    bottom: float
    column: int = 0


def color_matches(value: object, tolerance: float = 0.04) -> bool:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return False
    return all(abs(float(a) - b) <= tolerance for a, b in zip(value, TARGET_CMYK))


def find_markers(page: pdfplumber.page.Page) -> list[Marker]:
    words = page.extract_words(
        use_text_flow=True,
        x_tolerance=2,
        y_tolerance=2,
        extra_attrs=["non_stroking_color"],
    )
    found: list[Marker] = []
    for word in words:
        text = str(word["text"]).strip()
        if NUMBER_RE.fullmatch(text) and color_matches(word.get("non_stroking_color")):
            found.append(
                Marker(text, float(word["x0"]), float(word["top"]), float(word["bottom"]))
            )
    return found


def assign_columns(markers: list[Marker], page_width: float) -> tuple[list[Marker], int]:
    """Detect a two-column layout from two widely separated marker clusters."""
    positions = sorted({m.x0 for m in markers})
    gaps = [(positions[i + 1] - positions[i], i) for i in range(len(positions) - 1)]
    largest_gap, gap_index = max(gaps, default=(0.0, 0))
    column_count = 2 if largest_gap > page_width * 0.18 else 1
    split_x = (positions[gap_index] + positions[gap_index + 1]) / 2 if column_count == 2 else page_width
    assigned = [
        Marker(m.number, m.x0, m.top, m.bottom, int(column_count == 2 and m.x0 >= split_x))
        for m in markers
    ]
    return assigned, column_count


def extract(
    pdf_path: Path,
    output_dir: Path,
    scale: float = 2.5,
    top_padding: float = 10,
    bottom_padding: float = 6,
    side_margin: float = 55,
    page_bottom_margin: float = 55,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered_pdf = pdfium.PdfDocument(str(pdf_path))
    count = 0

    with pdfplumber.open(pdf_path) as document:
        for page_index, page in enumerate(document.pages):
            markers, column_count = assign_columns(find_markers(page), page.width)
            if not markers:
                continue

            rendered = rendered_pdf[page_index].render(scale=scale).to_pil()
            crop_x0, _crop_y0, crop_x1, crop_y1 = page.cropbox or page.mediabox
            crop_top = page.height - crop_y1
            x_scale = rendered.width / (crop_x1 - crop_x0)
            y_scale = rendered.height / (crop_y1 - _crop_y0)
            middle = page.width / 2

            for column in range(column_count):
                current_column = sorted(
                    (marker for marker in markers if marker.column == column),
                    key=lambda marker: marker.top,
                )
                for index, marker in enumerate(current_column):
                    next_top = (
                        current_column[index + 1].top
                        if index + 1 < len(current_column)
                        else page.height - page_bottom_margin
                    )

                    if column_count == 1:
                        left, right = side_margin, page.width - side_margin
                    elif column == 0:
                        left, right = side_margin, middle - 4
                    else:
                        left, right = middle - 4, page.width - side_margin

                    top = max(0, marker.top - top_padding)
                    bottom = min(page.height, next_top - bottom_padding)
                    if bottom <= top:
                        continue

                    pixel_box = (
                        max(0, round((left - crop_x0) * x_scale)),
                        max(0, round((top - crop_top) * y_scale)),
                        min(rendered.width, round((right - crop_x0) * x_scale)),
                        min(rendered.height, round((bottom - crop_top) * y_scale)),
                    )
                    image = rendered.crop(pixel_box)
                    filename = f"{marker.number}_p{page_index + 1:03d}.png"
                    image.save(output_dir / filename, optimize=True)
                    print(f"saved {filename}")
                    count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="input PDF path")
    parser.add_argument("-o", "--output", type=Path, default=Path("extracted_problems"))
    parser.add_argument("--scale", type=float, default=2.5, help="rendering scale (default: 2.5)")
    args = parser.parse_args()

    if not args.pdf.is_file():
        parser.error(f"PDF not found: {args.pdf}")
    total = extract(args.pdf, args.output, scale=args.scale)
    print(f"done: extracted {total} problem(s) into {args.output}")


if __name__ == "__main__":
    main()
