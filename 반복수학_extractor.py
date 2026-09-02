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


# 16:9 PPT의 좌상단 배치에서 20pt 상당 크기를 유지할 수 있는 최대 높이.
# 외곽 여백(위아래 24px씩)을 포함한 값이다.
MAX_IMAGE_HEIGHT = 760
OUTPUT_MARGIN = 24


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


@dataclass(frozen=True)
class TipRegion:
    x0: float
    top: float
    x1: float
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


def tip_regions(page) -> list[TipRegion]:
    """PDF의 청록색 ``tip`` 표식과 연노랑 설명 상자를 한 영역으로 찾는다."""
    extract_words = getattr(page, "extract_words", lambda: [])
    words = [word for word in extract_words() if str(word.get("text", "")).lower() == "tip"]
    regions = []
    for word in words:
        wx0, wtop, wx1, wbottom = map(
            float, (word["x0"], word["top"], word["x1"], word["bottom"])
        )
        backgrounds = []
        nearby_curves = []
        for curve in getattr(page, "curves", []):
            if not all(key in curve for key in ("x0", "x1", "top", "bottom")):
                continue
            x0, top, x1, bottom = map(
                float, (curve["x0"], curve["top"], curve["x1"], curve["bottom"])
            )
            if x0 <= wx1 + 8 and x1 >= wx0 - 8 and top <= wbottom + 10 and bottom >= wtop - 10:
                nearby_curves.append((x0, top, x1, bottom))
            color = curve.get("non_stroking_color")
            if not isinstance(color, (tuple, list)) or len(color) != 3:
                continue
            red, green, blue = map(float, color)
            is_tip_yellow = red > 0.88 and green > 0.88 and 0.60 < blue < 0.88
            if not is_tip_yellow:
                continue
            if x0 <= wx1 + 8 and x1 >= wx0 - 8 and top <= wbottom + 10 and bottom >= wtop - 2:
                backgrounds.append((x0, top, x1, bottom))
        if not backgrounds:
            continue
        background = max(backgrounds, key=lambda item: (item[2] - item[0]) * (item[3] - item[1]))
        marker_top = min((item[1] for item in nearby_curves), default=wtop)
        regions.append(TipRegion(
            min(wx0 - 6, background[0]) - 2,
            min(wtop, marker_top, background[1]) - 2,
            background[2] + 2,
            background[3] + 2,
        ))
    return sorted(regions, key=lambda region: region.top)


def remove_tip_bands(
    image: Image.Image,
    page,
    rendered: Image.Image,
    source_box,
    regions: list[TipRegion],
) -> Image.Image:
    """TIP 상자가 차지한 가로 띠를 삭제해 빈 공간까지 함께 접는다."""
    result = image.convert("RGB")
    source_px = pdf_box_to_pixels(page, rendered, source_box)
    for region in sorted(regions, key=lambda item: item.top, reverse=True):
        region_box = (region.x0, region.top, region.x1, region.bottom)
        region_px = pdf_box_to_pixels(page, rendered, region_box)
        if region_px[2] <= source_px[0] or region_px[0] >= source_px[2]:
            continue
        y0 = max(0, region_px[1] - source_px[1])
        y1 = min(result.height, region_px[3] - source_px[1])
        if y1 <= y0:
            continue
        collapsed = Image.new("RGB", (result.width, result.height - (y1 - y0)), "white")
        collapsed.paste(result.crop((0, 0, result.width, y0)), (0, 0))
        collapsed.paste(result.crop((0, y1, result.width, result.height)), (0, y0))
        result = collapsed
    return result


def prompt_header(
    page,
    rendered: Image.Image,
    marker: MainMarker,
    left: float,
    right: float,
    hard_bottom: float,
) -> Image.Image | None:
    """분할 이미지마다 반복할 큰 번호와 공통 발문을 추출한다."""
    boundaries = [
        sub.top for sub in sub_markers(page, left, right, marker.bottom, hard_bottom)
        if sub.top > marker.bottom + 2
    ]
    boundaries.extend(
        float(char["top"])
        for char in getattr(page, "chars", [])
        if str(char.get("text", "")) in "①②③④⑤"
        and left <= float(char["x0"]) < right
        and marker.bottom + 2 < float(char["top"]) < hard_bottom
    )
    if not boundaries:
        return None
    header_bottom = min(boundaries) - 3
    if header_bottom <= marker.bottom + 4:
        return None
    box = tight_box(page, left, right, marker.top - 4, header_bottom)
    if box is None:
        return None
    return rendered.crop(pdf_box_to_pixels(page, rendered, box)).convert("RGB")


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


def stack_panels(panels: list[Image.Image], gap: int = 18) -> Image.Image:
    """열을 넘어 이어지는 한 문제의 조각을 읽는 순서대로 세로 결합한다."""
    if len(panels) == 1:
        return panels[0]
    width = max(panel.width for panel in panels)
    height = sum(panel.height for panel in panels) + gap * (len(panels) - 1)
    result = Image.new("RGB", (width, height), "white")
    y = 0
    for panel in panels:
        result.paste(panel, (0, y))
        y += panel.height + gap
    return result


def split_at_whitespace(image: Image.Image, max_height: int = MAX_IMAGE_HEIGHT) -> list[Image.Image]:
    """20pt 크기를 유지하도록 긴 문제를 가로 공백에서 여러 이미지로 나눈다."""
    rgb = image.convert("RGB")
    if rgb.height <= max_height:
        return [rgb]

    # 기존 외곽 여백을 제거한 뒤 각 조각에 동일한 여백을 다시 준다.
    margin = OUTPUT_MARGIN
    inner = rgb.crop((margin, margin, rgb.width - margin, rgb.height - margin))
    part_height = max_height - margin * 2
    pixels = np.asarray(inner)
    ink_per_row = np.count_nonzero(np.min(pixels, axis=2) < 245, axis=1)
    parts = []
    start = 0
    while inner.height - start > part_height:
        target = start + part_height
        search_start = max(start + 120, target - 150)
        # 가능한 한 목표 높이에 가까운 빈 행을 선택한다. 완전히 빈 행이
        # 없으면 잉크가 가장 적은 행을 사용하되 수식/글자 중간은 피한다.
        densities = ink_per_row[search_start:target + 1]
        minimum = int(densities.min())
        quiet_mask = densities <= max(minimum, max(2, inner.width // 300))
        # 짧은 빈틈보다 소문항 사이의 넓은 공백을 우선한다. 그러면 다음
        # 소문항 제목만 앞 페이지 끝에 남는 widow 현상을 막을 수 있다.
        quiet_runs = []
        run_start = None
        for offset, quiet in enumerate(np.append(quiet_mask, False)):
            if quiet and run_start is None:
                run_start = offset
            elif not quiet and run_start is not None:
                quiet_runs.append((offset - run_start, run_start, offset - 1))
                run_start = None
        if quiet_runs:
            _, run_begin, run_end = max(quiet_runs, key=lambda run: (run[0], run[2]))
            cut = search_start + (run_begin + run_end) // 2
        else:
            cut = target
        if cut <= start + 80:
            cut = target
        parts.append(add_margin(inner.crop((0, start, inner.width, cut)), margin))
        start = cut
    parts.append(add_margin(inner.crop((0, start, inner.width, inner.height)), margin))
    return parts


def split_with_repeated_header(
    image: Image.Image,
    header: Image.Image | None,
    max_height: int = MAX_IMAGE_HEIGHT,
) -> list[Image.Image]:
    """긴 문제를 나누고 두 번째 조각부터 큰 번호·공통 발문을 다시 붙인다."""
    if image.height <= max_height or header is None:
        return split_at_whitespace(image, max_height)
    reserve = header.height + 18
    reduced_height = max(260, max_height - reserve)
    parts = split_at_whitespace(image, reduced_height)
    if len(parts) <= 1:
        return parts
    repeated = [parts[0]]
    for part in parts[1:]:
        body = part.crop((OUTPUT_MARGIN, OUTPUT_MARGIN, part.width - OUTPUT_MARGIN, part.height - OUTPUT_MARGIN))
        repeated.append(add_margin(stack_left(header, body), OUTPUT_MARGIN))
    return repeated


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
            page_tip_regions = tip_regions(page)
            page_serial = 0
            divider_lines = [
                line for line in page.lines
                if float(line.get("height", 0)) > 300
                and abs(float(line["x1"]) - float(line["x0"])) < 2
                and page.width * 0.4 < float(line["x0"]) < page.width * 0.6
            ]
            divider = float(divider_lines[0]["x0"]) if divider_lines else page.width / 2
            markers_by_column = {
                column: sorted((marker for marker in markers if marker.column == column), key=lambda marker: marker.top)
                for column in (0, 1)
            }
            # 왼쪽 마지막 문제의 소문항이 오른쪽 단 상단으로 이어지는지 판별한다.
            continuation_box = None
            continuation_owner = None
            left_markers = markers_by_column[0]
            right_markers = markers_by_column[1]
            if left_markers and right_markers:
                exercise_top = min(marker.top for marker in markers)
                first_right = right_markers[0]
                right_left, right_right = divider + 4, page.width - 45
                continuation_subs = sub_markers(
                    page, right_left, right_right, exercise_top, first_right.top - 5
                )
                if continuation_subs and first_right.top - exercise_top > 80:
                    continuation_box = tight_box(
                        page, right_left, right_right, exercise_top, first_right.top - 5
                    )
                    continuation_owner = left_markers[-1]
            for column in (0, 1):
                left = 45 if column == 0 else divider + 4
                right = divider - 4 if column == 0 else page.width - 45
                column_markers = markers_by_column[column]
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
                    header = prompt_header(page, rendered, marker, left, right, hard_bottom)
                    panel_boxes = [box]
                    if marker == continuation_owner and continuation_box is not None:
                        panel_boxes.append(continuation_box)
                    panels = [
                        remove_tip_bands(
                            rendered.crop(pdf_box_to_pixels(page, rendered, panel_box)),
                            page,
                            rendered,
                            panel_box,
                            page_tip_regions,
                        )
                        for panel_box in panel_boxes
                    ]
                    image = add_margin(stack_panels(panels), OUTPUT_MARGIN)
                    if concept_basic_page:
                        image = clean_concept_basic_image(image)
                    page_serial += 1
                    parts = split_with_repeated_header(image, header)
                    for part_index, part in enumerate(parts, 1):
                        part_suffix = f"-{part_index:02d}" if len(parts) > 1 else ""
                        filename = f"{page_index + 1:03d}p_{page_serial:03d}{part_suffix}.png"
                        destination = output_dir / filename
                        part.save(destination, "PNG", optimize=True)
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
