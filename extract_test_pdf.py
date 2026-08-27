"""Reviewed extraction for the one-page second test PDF."""

from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont


SOURCE = Path(r"C:\Users\user\Downloads\ㄹㅁㄴㅇ.pdf")
OUTPUT = Path("output/second_test")
SCALE = 3.0

# Reviewed semantic bounds. Boxes that group answer choices or expressions are
# part of the problem and therefore determine the crop boundary too.
PROBLEMS = {
    # Keep the inner black expression box, but exclude the decorative outer card.
    "003": (137.5, 451.0, 536.2, 531.0),
    "004": (130.0, 660.0, 555.0, 727.0),
}


def to_pixels(page, rendered, box):
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


def with_margin(image, margin=24):
    result = Image.new("RGB", (image.width + 2 * margin, image.height + 2 * margin), "white")
    result.paste(image, (margin, margin))
    return result


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(SOURCE))
    with pdfplumber.open(SOURCE) as layout:
        rendered = pdf[0].render(scale=SCALE).to_pil().convert("RGB")
        files = []
        for number, box in PROBLEMS.items():
            image = with_margin(rendered.crop(to_pixels(layout.pages[0], rendered, box)))
            destination = OUTPUT / f"{number}.png"
            image.save(destination, optimize=True)
            files.append(destination)

    preview = Image.new("RGB", (1400, 1000), (232, 235, 241))
    draw = ImageDraw.Draw(preview)
    font = ImageFont.load_default(size=24)
    y = 20
    for path in files:
        image = Image.open(path).convert("RGB")
        image.thumbnail((1300, 400))
        draw.text((40, y), path.stem, fill=(25, 30, 40), font=font)
        preview.paste(image, ((preview.width - image.width) // 2, y + 38))
        y += 480
    preview.save(OUTPUT.parent / "second_test_preview.jpg", quality=90, optimize=True)


if __name__ == "__main__":
    main()
