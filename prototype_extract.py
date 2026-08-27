"""Create four reviewed problem crops from the supplied workbook sample.

These crops are a calibration set for the later automatic layout detector.
Coordinates use the PDF media-box coordinate system used by pdfplumber.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw, ImageFont


@dataclass(frozen=True)
class ReviewedProblem:
    number: str
    page: int
    box: tuple[float, float, float, float]


# left, top, right, bottom in pdfplumber coordinates
REVIEWED = (
    # Horizontal bounds follow text only; the decorative outer box is ignored.
    ReviewedProblem("001", 2, (137.5, 338, 480.2, 433)),
    ReviewedProblem("015", 4, (82, 138, 296, 245)),
    ReviewedProblem("051", 6, (110, 138, 316, 238)),
    ReviewedProblem("059", 7, (312, 138, 558, 335)),
)


def pdf_box_to_pixels(page, rendered: Image.Image, box):
    crop_x0, crop_y0, crop_x1, crop_y1 = page.cropbox or page.mediabox
    crop_top = page.height - crop_y1
    sx = rendered.width / (crop_x1 - crop_x0)
    sy = rendered.height / (crop_y1 - crop_y0)
    left, top, right, bottom = box
    return (
        max(0, round((left - crop_x0) * sx)),
        max(0, round((top - crop_top) * sy)),
        min(rendered.width, round((right - crop_x0) * sx)),
        min(rendered.height, round((bottom - crop_top) * sy)),
    )


def add_white_margin(image: Image.Image, margin: int = 24) -> Image.Image:
    result = Image.new("RGB", (image.width + margin * 2, image.height + margin * 2), "white")
    result.paste(image.convert("RGB"), (margin, margin))
    return result


def make_preview(files: list[Path], destination: Path) -> None:
    cards = []
    font = ImageFont.load_default(size=26)
    for path in files:
        image = Image.open(path).convert("RGB")
        image.thumbnail((900, 570))
        card = Image.new("RGB", (960, 650), (241, 243, 247))
        draw = ImageDraw.Draw(card)
        draw.text((28, 18), path.stem, fill=(25, 30, 40), font=font)
        card.paste(image, ((card.width - image.width) // 2, 64))
        cards.append(card)
    preview = Image.new("RGB", (1920, 1300), (225, 229, 236))
    for index, card in enumerate(cards):
        preview.paste(card, ((index % 2) * 960, (index // 2) * 650))
    preview.save(destination, quality=90, optimize=True)


def extract(pdf_path: Path, output_dir: Path, scale: float = 3.0) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    results = []
    with pdfplumber.open(pdf_path) as layout:
        for problem in REVIEWED:
            page_index = problem.page - 1
            rendered = pdf[page_index].render(scale=scale).to_pil().convert("RGB")
            crop = rendered.crop(pdf_box_to_pixels(layout.pages[page_index], rendered, problem.box))
            crop = add_white_margin(crop)
            destination = output_dir / f"{problem.number}_reviewed.png"
            crop.save(destination, optimize=True)
            results.append(destination)
    make_preview(results, output_dir.parent / "prototype_preview.jpg")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("output/prototype"))
    args = parser.parse_args()
    files = extract(args.pdf, args.output)
    print("created:", ", ".join(path.name for path in files))


if __name__ == "__main__":
    main()
