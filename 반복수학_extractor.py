"""반복수학 교재 전용 문제 추출기.

큰 색상 번호 하나를 기준으로 발문과 (1), (2), ... 소문항 전체를
한 장으로 자른다. 중학·고등·반복 파워의 개념 기본 문제에도 동일하게
적용하며, ①~⑤ 선택지가 있는 문제도 큰 번호 기준으로 유지한다.
"""

from __future__ import annotations

import re
import argparse
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import numpy as np
from PIL import Image

from pungsanja_extractor import add_margin, is_colored_text, pdf_box_to_pixels


@dataclass(frozen=True)
class MainMarker:
    number: str
    x0: float
    top: float
    x1: float
    bottom: float
    column: int


@dataclass(frozen=True)
class SubMarker:
    number: str
    x0: float
    top: float
    bottom: float


def main_markers(page) -> list[MainMarker]:
    candidates = []
    for char in page.chars:
        text = str(char.get("text", ""))
        size = float(char.get("size", 0))
        if text.isdigit() and 16 <= size <= 25 and is_colored_text(char.get("non_stroking_color")):
            candidates.append(char)
    candidates.sort(key=lambda char: (round(float(char["top"]), 1), float(char["x0"])))

    runs: list[list[dict]] = []
    for char in candidates:
        if (
            not runs
            or abs(float(char["top"]) - float(runs[-1][-1]["top"])) > 2
            or float(char["x0"]) < float(runs[-1][-1]["x0"])
            or float(char["x0"]) - float(runs[-1][-1]["x1"]) > 3
        ):
            runs.append([char])
        else:
            runs[-1].append(char)
    return [
        MainMarker(
            "".join(str(char["text"]) for char in run),
            min(float(char["x0"]) for char in run),
            min(float(char["top"]) for char in run),
            max(float(char["x1"]) for char in run),
            max(float(char["bottom"]) for char in run),
            int(sum(float(char["x0"]) for char in run) / len(run) >= page.width / 2),
        )
        for run in runs
    ]


def sub_markers(page, left: float, right: float, top: float, bottom: float) -> list[SubMarker]:
    chars = [
        char for char in page.chars
        if left <= float(char["x0"]) < right and top <= float(char["top"]) < bottom
    ]
    chars.sort(key=lambda char: (round(float(char["top"]), 1), float(char["x0"])))
    result = []
    for index, char in enumerate(chars):
        # A true sub-question marker is placed at the left indentation. Parentheses
        # in coordinates such as A(2), formulas, and diagrams occur farther right.
        text = str(char.get("text", ""))
        # The book stores (1), (2), ... as Unicode parenthesized digits
        # U+2474..U+2487 rather than three separate characters.
        if len(text) == 1 and 0x2474 <= ord(text) <= 0x2487 and float(char["x0"]) <= left + 80:
            number = str(ord(text) - 0x2473)
            result.append(SubMarker(number, float(char["x0"]), float(char["top"]), float(char["bottom"])))
            continue
        if text != "(" or float(char["x0"]) > left + 45:
            continue
        same_line = [
            other for other in chars[index + 1:index + 5]
            if abs(float(other["top"]) - float(char["top"])) < 2
            and 0 <= float(other["x0"]) - float(char["x1"]) < 15
        ]
        joined = "(" + "".join(str(other.get("text", "")) for other in same_line)
        match = re.match(r"\(([1-9])\)", joined)
        if match:
            result.append(SubMarker(match.group(1), float(char["x0"]), float(char["top"]), float(char["bottom"])))
    deduped = {(round(marker.top, 1), marker.number): marker for marker in result}
    return sorted(deduped.values(), key=lambda marker: marker.top)


def has_circled_choices(page, left: float, right: float, top: float, bottom: float) -> bool:
    choice_chars = set("①②③④⑤")
    return any(
        str(char.get("text", "")) in choice_chars
        and left <= float(char["x0"]) < right
        and top <= float(char["top"]) < bottom
        for char in page.chars
    )


def tight_box(page, left: float, right: float, top: float, bottom: float):
    horizontal_margin = 8.0
    vertical_margin = 4.0
    objects = []
    for char in page.chars:
        center_x = (float(char["x0"]) + float(char["x1"])) / 2
        center_y = (float(char["top"]) + float(char["bottom"])) / 2
        if left <= center_x < right and top <= center_y < bottom:
            objects.append((float(char["x0"]), float(char["top"]), float(char["x1"]), float(char["bottom"])))
    for collection in (page.rects, page.curves, page.images):
        for obj in collection:
            if not all(key in obj for key in ("x0", "x1", "top", "bottom")):
                continue
            x0, y0, x1, y1 = map(float, (obj["x0"], obj["top"], obj["x1"], obj["bottom"]))
            object_width = x1 - x0
            object_height = y1 - y0
            region_width = right - left
            region_height = bottom - top
            # 페이지 가장자리의 세로 장식과 문항 사이의 긴 가로 구분선은
            # 소문항 내용이 아니다. 이 객체가 전체 페이지까지 crop을
            # 끌어내리지 않도록 제외한다.
            if object_height > max(region_height * 1.5, 90) and object_width < region_width * 0.4:
                continue
            if (
                x0 <= left + 12
                and object_height > region_height * 0.6
                and object_width < region_width * 0.2
            ):
                continue
            if object_width > region_width * 0.9 and object_height < 4:
                continue
            if x1 > left and x0 < right and y1 > top and y0 < bottom:
                objects.append((x0, y0, x1, y1))
    if not objects:
        return None
    return (
        max(left, min(item[0] for item in objects) - horizontal_margin),
        max(top, min(item[1] for item in objects) - vertical_margin),
        min(right, max(item[2] for item in objects) + horizontal_margin),
        min(bottom, max(item[3] for item in objects) + vertical_margin),
    )


def stack_left(header: Image.Image, body: Image.Image) -> Image.Image:
    """공통 숫자 발문 아래에 소문항을 좌측 정렬로 결합한다."""
    width = max(header.width, body.width)
    result = Image.new("RGB", (width, header.height + body.height + 18), "white")
    result.paste(header, (0, 0))
    result.paste(body, (0, header.height + 18))
    return result


def clean_concept_basic_image(image: Image.Image) -> Image.Image:
    """개념 기본 문제의 연한 살구색 페이지 장식을 지우고 내용에 맞춰 재단한다."""
    pixels = np.asarray(image.convert("RGB")).copy()
    red = pixels[..., 0].astype(np.int16)
    green = pixels[..., 1].astype(np.int16)
    blue = pixels[..., 2].astype(np.int16)
    peach = (
        (red > 235)
        & (green > 190)
        & (green < 248)
        & (blue > 165)
        & (blue < 238)
        & (red - green > 5)
        & (green - blue > 5)
    )
    pixels[peach] = 255

    content = np.min(pixels, axis=2) < 245
    ys, xs = np.where(content)
    if len(xs) == 0:
        return Image.fromarray(pixels, "RGB")
    padding = 8
    x0 = max(0, int(xs.min()) - padding)
    y0 = max(0, int(ys.min()) - padding)
    x1 = min(pixels.shape[1], int(xs.max()) + padding + 1)
    y1 = min(pixels.shape[0], int(ys.max()) + padding + 1)
    trimmed = Image.fromarray(pixels[y0:y1, x0:x1], "RGB")
    return add_margin(trimmed, 24)


def extract(pdf_path: Path, output_dir: Path, scale: float = 3.0) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    renderer = pdfium.PdfDocument(str(pdf_path))
    saved = []
    with pdfplumber.open(pdf_path) as document:
        for page_index, page in enumerate(document.pages):
            markers = main_markers(page)
            if not markers:
                continue
            concept_basic_page = "개념기본문제" in re.sub(r"\s+", "", page.extract_text() or "")
            rendered = renderer[page_index].render(scale=scale).to_pil().convert("RGB")
            page_serial = 0
            divider_lines = [
                line for line in page.lines
                if float(line.get("height", 0)) > 300
                and abs(float(line["x1"]) - float(line["x0"])) < 2
                and page.width * 0.4 < float(line["x0"]) < page.width * 0.6
            ]
            divider = float(divider_lines[0]["x0"]) if divider_lines else page.width / 2
            for column in (0, 1):
                left = 45 if column == 0 else divider + 4
                right = divider - 4 if column == 0 else page.width - 45
                column_markers = sorted((marker for marker in markers if marker.column == column), key=lambda marker: marker.top)
                for marker_index, marker in enumerate(column_markers):
                    hard_bottom = (
                        column_markers[marker_index + 1].top - 5
                        if marker_index + 1 < len(column_markers)
                        else page.height - 90
                    )
                    # 소문항 위치와 관계없이 다음 큰 번호 직전까지 한 번만 저장한다.
                    # 원본 배치를 유지하므로 발문 반복/소문항 합성은 하지 않는다.
                    box = tight_box(page, left, right, marker.top - 4, hard_bottom)
                    if box is None:
                        continue
                    page_serial += 1
                    filename = f"{page_index + 1:03d}p_{page_serial:03d}.png"
                    image = rendered.crop(pdf_box_to_pixels(page, rendered, box))
                    image = add_margin(image, 24)
                    if concept_basic_page:
                        image = clean_concept_basic_image(image)
                    destination = output_dir / filename
                    image.save(destination, "PNG", optimize=True)
                    saved.append(destination)
                    print(f"saved {filename}: main {marker.number}")
    renderer.close()
    return saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="반복수학 PDF 파일")
    parser.add_argument("-o", "--output", type=Path, default=Path("output/반복수학"))
    args = parser.parse_args()
    results = extract(args.pdf, args.output)
    print(f"done: {len(results)} image(s)")
