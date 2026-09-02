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
# (1), (2)처럼 번호가 붙은 소문항은 그림/풀이 박스를 한 장에 유지하는 것을 우선한다.
SUBQUESTION_MAX_HEIGHT = 1200
OUTPUT_MARGIN = 24
CONTINUATION_GAP = 18


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


def _is_type_header_number(page, run: list[dict]) -> bool:
    """``유형 03``처럼 문제 번호가 아닌 코너 제목 숫자를 제외한다."""
    if not run:
        return False
    run_x0 = min(float(char["x0"]) for char in run)
    run_top = min(float(char["top"]) for char in run)
    run_bottom = max(float(char["bottom"]) for char in run)
    nearby = [
        char for char in page.chars
        if float(char["x1"]) <= run_x0 + 1
        and run_x0 - 70 <= float(char["x0"]) <= run_x0
        and abs(float(char["top"]) - run_top) <= 4
        and abs(float(char["bottom"]) - run_bottom) <= 6
    ]
    nearby.sort(key=lambda char: float(char["x0"]))
    label = re.sub(r"\s+", "", "".join(str(char.get("text", "")) for char in nearby))
    return "유형" in label


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
        if not _is_type_header_number(page, run)
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


def point_regions(page) -> list[TipRegion]:
    """``풍쌤 POINT`` 제목과 이어진 연한 파란 설명 상자를 찾는다."""
    extract_words = getattr(page, "extract_words", lambda: [])
    words = list(extract_words())
    labels = [word for word in words if str(word.get("text", "")).strip() == "풍쌤"]
    following_problem_tops = []
    if hasattr(page, "chars") and hasattr(page, "width"):
        following_problem_tops = [marker.top for marker in main_markers(page)]
    regions = []
    for word in labels:
        wx0, wtop, wx1, wbottom = map(
            float, (word["x0"], word["top"], word["x1"], word["bottom"])
        )
        # 다른 본문에 우연히 등장한 '풍쌤'은 지우지 않는다. 같은 줄 바로
        # 오른쪽에 POINT가 있는 전용 코너만 대상으로 삼는다.
        has_point = any(
            str(other.get("text", "")).strip().upper() == "POINT"
            and 0 <= float(other["x0"]) - wx1 <= 18
            and abs(float(other["top"]) - wtop) <= 4
            for other in words
        )
        if not has_point:
            continue

        nearby = []
        for curve in getattr(page, "curves", []):
            if not all(key in curve for key in ("x0", "x1", "top", "bottom")):
                continue
            x0, top, x1, bottom = map(
                float, (curve["x0"], curve["top"], curve["x1"], curve["bottom"])
            )
            if (
                x0 <= wx1 + 8
                and x1 >= wx0 - 8
                and wtop - 15 <= top <= wbottom + 20
                and bottom <= wbottom + 120
                and x1 - x0 >= 20
            ):
                nearby.append((x0, top, x1, bottom))
        if not nearby:
            continue
        background = max(
            nearby, key=lambda item: (item[2] - item[0]) * (item[3] - item[1])
        )
        # 가장 큰 곡선이 본문 배경이고, 같은 코너의 작은 곡선들이 제목
        # 탭을 구성한다. 둘을 합친 전체 세로 띠를 접어 빈 공간도 없앤다.
        related = [
            item for item in nearby
            if item[0] <= background[2] and item[2] >= background[0]
        ]
        next_problem_top = min(
            (top for top in following_problem_tops if top > wtop + 5),
            default=float("inf"),
        )
        content_scan_bottom = min(wtop + 160, next_problem_top - 4)
        content_bottom = max(
            (
                float(other["bottom"])
                for other in words
                if background[0] - 5 <= float(other["x0"]) <= background[2] + 5
                and wtop <= float(other["top"]) <= content_scan_bottom
            ),
            default=background[3],
        )
        region_bottom = max(
            max(item[3] for item in related) + 2,
            content_bottom + 8,
        )
        if next_problem_top != float("inf"):
            # 상단 POINT와 첫 문제 사이가 가까운 판본에서는 설명을 찾는
            # 160pt 탐색 범위가 문제 선지까지 닿는다. 다음 큰 번호를 절대
            # 넘어가지 않게 막아 ①~⑤가 함께 지워지는 것을 방지한다.
            region_bottom = min(region_bottom, next_problem_top - 2)
        regions.append(TipRegion(
            min(item[0] for item in related) - 2,
            # 제목 탭 왼쪽의 작은 삼각 장식은 별도 곡선이라 후보에서
            # 빠질 수 있다. 제목 글자보다 20pt 위부터 지워 잔상을 막는다.
            min(wtop - 20, min(item[1] for item in related) - 4),
            max(item[2] for item in related) + 2,
            region_bottom,
        ))
    deduped = {
        (round(region.x0, 1), round(region.top, 1)): region
        for region in regions
    }
    return sorted(deduped.values(), key=lambda region: region.top)


def remove_tip_bands(
    image: Image.Image,
    page,
    rendered: Image.Image,
    source_box,
    regions: list[TipRegion],
) -> Image.Image:
    """TIP/풍쌤 POINT 상자가 차지한 가로 띠를 삭제해 함께 접는다."""
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


def trim_bottom_whitespace(image: Image.Image, padding: int = 12) -> Image.Image:
    """보조 상자를 지운 뒤 아래쪽에 남는 빈 공간만 정리한다."""
    rgb = image.convert("RGB")
    pixels = np.asarray(rgb)
    ink = np.min(pixels, axis=2) < 245
    rows = np.flatnonzero(np.any(ink, axis=1))
    if len(rows) == 0:
        return rgb.crop((0, 0, rgb.width, 1))
    bottom = min(rgb.height, int(rows[-1]) + padding + 1)
    return rgb.crop((0, 0, rgb.width, bottom))


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
    # 어떤 판본은 큰 번호/발문 바로 아래(약 4pt)에 첫 소문항이 붙는다.
    # 소문항 위 1pt까지만 포함하면 발문 행은 온전히 남기면서 번호가
    # 소문항에 섞이지 않는다.
    header_bottom = min(boundaries) - 1
    if header_bottom <= marker.bottom:
        return None
    box = tight_box(page, left, right, marker.top - 4, header_bottom)
    if box is None:
        return None
    return rendered.crop(pdf_box_to_pixels(page, rendered, box)).convert("RGB")


def prompt_body_start(
    page,
    marker: MainMarker,
    left: float,
    right: float,
    hard_bottom: float,
) -> float | None:
    """공통 발문 아래 첫 소문항/선택지의 시작 y좌표를 찾는다."""
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
    return min(boundaries) if boundaries else None


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


def trim_horizontal_whitespace(image: Image.Image, padding: int = 0) -> Image.Image:
    """좌우의 순수 흰 여백만 제거한다.

    분할 후 반복되는 공통 발문과 본문은 서로 다른 원본 crop 폭을 사용한다.
    이 때문에 발문 자체에는 왼쪽 흰 여백이 남고, 오른쪽 단에서 이어진
    소문항은 더 왼쪽에서 시작해 발문이 오른쪽으로 밀려 보일 수 있다.
    두 이미지를 결합하기 전에 실제 잉크 시작점을 맞추기 위해 사용한다.
    """
    rgb = image.convert("RGB")
    pixels = np.asarray(rgb)
    ink = np.min(pixels, axis=2) < 245
    cols = np.flatnonzero(np.any(ink, axis=0))
    if len(cols) == 0:
        return rgb.crop((0, 0, 1, rgb.height))
    left = max(0, int(cols[0]) - padding)
    right = min(rgb.width, int(cols[-1]) + padding + 1)
    return rgb.crop((left, 0, right, rgb.height))


def stack_left(header: Image.Image, body: Image.Image) -> Image.Image:
    """공통 발문과 후속 소문항의 실제 내용 시작점을 좌측 정렬한다.

    단순히 이미지 캔버스의 x=0에 붙이는 것이 아니라 두 이미지의 흰색
    좌측 여백을 먼저 제거한다. 따라서 오른쪽 단에서 이어진 소문항처럼
    body의 실제 시작점이 더 왼쪽인 경우에도 발문이 오른쪽으로 밀리지 않는다.
    """
    header = trim_horizontal_whitespace(header)
    body = trim_horizontal_whitespace(body)
    width = max(header.width, body.width)
    result = Image.new(
        "RGB", (width, header.height + body.height + CONTINUATION_GAP), "white"
    )
    result.paste(header, (0, 0))
    result.paste(body, (0, header.height + CONTINUATION_GAP))
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


def _cut_crosses_vertical_border(
    pixels: np.ndarray,
    cut: int,
    half_window: int = 24,
) -> bool:
    """박스의 좌우 세로 테두리 한가운데에서 자르는 것을 막는다."""
    height = pixels.shape[0]
    y0 = max(0, cut - half_window)
    y1 = min(height, cut + half_window + 1)
    if y1 - y0 < 12:
        return False
    ink = np.min(pixels[y0:y1], axis=2) < 225
    # 같은 x열에 세로선이 창 높이의 대부분 존재하면 박스/표의 테두리로 본다.
    vertical_counts = np.count_nonzero(ink, axis=0)
    return bool(np.any(vertical_counts >= int((y1 - y0) * 0.78)))


def _source_subquestion_boxes(
    page,
    left: float,
    right: float,
    top: float,
    bottom: float,
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """한 단 안의 (1), (2), ...를 각각 끊기지 않는 원본 영역으로 만든다."""
    subs = sub_markers(page, left, right, top, bottom)
    result = []
    for index, sub in enumerate(subs):
        end = subs[index + 1].top - 3 if index + 1 < len(subs) else bottom
        box = tight_box(page, left, right, sub.top - 2, end)
        if box is not None:
            result.append((sub.number, box))
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
        # 완전히 빈 행이 있으면 그것만 우선 사용한다. 박스의 좌우 세로선만
        # 남은 행(잉크 1~2px)을 빈 공간으로 오인하면 박스 중앙이 잘린다.
        if np.any(densities == 0):
            quiet_mask = densities == 0
        else:
            quiet_mask = densities <= minimum

        quiet_runs = []
        run_start = None
        for offset, quiet in enumerate(np.append(quiet_mask, False)):
            if quiet and run_start is None:
                run_start = offset
            elif not quiet and run_start is not None:
                quiet_runs.append((offset - run_start, run_start, offset - 1))
                run_start = None

        cut = None
        # 넓고 목표점에 가까운 빈 구간부터 확인하되, 세로 박스 테두리를
        # 가로지르는 위치는 후보에서 제외한다.
        for _length, run_begin, run_end in sorted(
            quiet_runs,
            key=lambda run: (run[0], run[2]),
            reverse=True,
        ):
            candidate = search_start + (run_begin + run_end) // 2
            if not _cut_crosses_vertical_border(pixels, candidate):
                cut = candidate
                break

        if cut is None:
            # 최후 수단에서도 박스 테두리 중앙은 피한다.
            candidates = list(range(target, search_start - 1, -1))
            cut = next(
                (candidate for candidate in candidates
                 if not _cut_crosses_vertical_border(pixels, candidate)),
                target,
            )
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
    reserve = header.height + CONTINUATION_GAP
    reduced_height = max(260, max_height - reserve)
    parts = [
        part for part in split_at_whitespace(image, reduced_height)
        if has_meaningful_ink(part)
    ]
    if len(parts) <= 1:
        return parts
    repeated = [parts[0]]
    for part in parts[1:]:
        body = part.crop((OUTPUT_MARGIN, OUTPUT_MARGIN, part.width - OUTPUT_MARGIN, part.height - OUTPUT_MARGIN))
        body = trim_vertical_whitespace(body)
        repeated.append(add_margin(stack_left(header, body), OUTPUT_MARGIN))
    return repeated


def has_meaningful_ink(image: Image.Image, minimum_pixels: int = 100) -> bool:
    """긴 공백만 잘려 생긴 빈 조각인지 판별한다."""
    pixels = np.asarray(image.convert("RGB"))
    return int(np.count_nonzero(np.min(pixels, axis=2) < 245)) >= minimum_pixels


def trim_vertical_whitespace(image: Image.Image, padding: int = 0) -> Image.Image:
    """후속 조각의 바깥쪽 세로 공백을 없애 본문 시작 위치를 고정한다."""
    rgb = image.convert("RGB")
    pixels = np.asarray(rgb)
    ink = np.min(pixels, axis=2) < 245
    rows = np.flatnonzero(np.any(ink, axis=1))
    if len(rows) == 0:
        return rgb.crop((0, 0, rgb.width, 1))
    top = max(0, int(rows[0]) - padding)
    bottom = min(rgb.height, int(rows[-1]) + padding + 1)
    return rgb.crop((0, top, rgb.width, bottom))


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


def continuation_owner_by_sequence(
    page,
    left_markers: list[MainMarker],
    left: float,
    right: float,
    first_right_number: str,
    page_bottom: float,
) -> MainMarker | None:
    """오른쪽 첫 소문항 번호와 이어지는 왼쪽 문제를 찾는다.

    예를 들어 왼쪽 문제의 마지막 소문항이 (1)이면 오른쪽 (2)를,
    마지막이 (4)이면 오른쪽 (5)를 가진 단을 같은 문제로 연결한다.
    """
    try:
        expected_previous = int(first_right_number) - 1
    except (TypeError, ValueError):
        return None
    matches = []
    for index, marker in enumerate(left_markers):
        hard_bottom = (
            left_markers[index + 1].top - 5
            if index + 1 < len(left_markers)
            else page_bottom
        )
        subs = sub_markers(page, left, right, marker.bottom, hard_bottom)
        numeric = [int(sub.number) for sub in subs if str(sub.number).isdigit()]
        if numeric and numeric[-1] == expected_previous:
            matches.append(marker)
    return max(matches, key=lambda marker: marker.top) if matches else None


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
            page_tip_regions = tip_regions(page) + point_regions(page)
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
            # 왼쪽 문제의 소문항이 오른쪽 단으로 이어지는지 판별한다.
            #
            # 기존 코드는 오른쪽 단에 '다음 큰 문제 번호'가 있을 때만 그 번호
            # 위쪽을 continuation으로 보았다. 그래서 한 문제(예: 027)가 (1)~(4)는
            # 왼쪽, (5)~(8)은 오른쪽에 배치되고 오른쪽 단에 별도 큰 번호/발문이
            # 전혀 없는 페이지에서는 오른쪽 절반이 통째로 누락되었다.
            continuation_box = None
            continuation_owner = None
            left_markers = markers_by_column[0]
            right_markers = markers_by_column[1]
            if left_markers:
                exercise_top = min(marker.top for marker in markers)
                right_left, right_right = divider + 4, page.width - 45

                # 오른쪽에 다음 큰 문제가 있으면 그 직전까지만, 없으면 페이지
                # 본문 하단까지 스캔한다. 후자가 '상단 발문 없는 2단 문제' 케이스다.
                continuation_bottom = (
                    right_markers[0].top - 5 if right_markers else page.height - 90
                )
                # 오른쪽 단에 큰 번호/공통 발문이 전혀 없는 판본은 첫 소문항이
                # 왼쪽의 큰 번호보다 위에서 시작할 수도 있다. (예: 오른쪽 (5)가
                # 왼쪽 027보다 약 30pt 위에 배치됨) 이 경우만 위쪽을 넉넉히
                # 탐색한다.
                continuation_scan_top = (
                    exercise_top if right_markers else max(0, exercise_top - 90)
                )
                continuation_subs = sub_markers(
                    page, right_left, right_right, continuation_scan_top, continuation_bottom
                )

                if continuation_subs and continuation_bottom - exercise_top > 80:
                    first_sub_top = continuation_subs[0].top

                    if right_markers:
                        # 기존 판본의 읽기 순서를 유지한다. 오른쪽 첫 큰 번호보다
                        # 위의 소문항은 왼쪽 단 마지막 큰 문제의 이어지는 부분이다.
                        continuation_owner = left_markers[-1]
                    else:
                        # 세로 위치가 아니라 소문항 번호의 연속성으로 연결한다.
                        # 왼쪽 (1) 뒤의 오른쪽 (2), 왼쪽 (1)~(4) 뒤의
                        # 오른쪽 (5)처럼 번호가 이어지는 큰 문제를 우선한다.
                        continuation_owner = continuation_owner_by_sequence(
                            page,
                            left_markers,
                            45,
                            divider - 4,
                            continuation_subs[0].number,
                            page.height - 90,
                        )
                        if continuation_owner is None:
                            preceding = [
                                marker for marker in left_markers
                                if marker.top <= first_sub_top + 12
                            ]
                            continuation_owner = (
                                max(preceding, key=lambda marker: marker.top)
                                if preceding else left_markers[0]
                            )

                    # 공통 발문이 없는 오른쪽 단에서는 실제 첫 소문항 바로 위부터
                    # 잘라 불필요한 상단 공백까지 함께 붙는 것을 막는다.
                    continuation_box = tight_box(
                        page,
                        right_left,
                        right_right,
                        max(0, first_sub_top - 4),
                        continuation_bottom,
                    )
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
                    body_start = prompt_body_start(page, marker, left, right, hard_bottom)

                    # 공통 발문 + 소문항 구조는 발문과 본문을 별도로 재단한다.
                    # 원본에서는 큰 번호 때문에 본문 (1), (2)...가 안쪽으로
                    # 들어가 있는데, 분할 후 다른 단의 소문항과 합치면 그 들여쓰기가
                    # 서로 달라 보일 수 있다. 각 본문 패널을 실제 잉크 시작점까지
                    # 따로 당긴 뒤 같은 x=0 기준선에 맞춰 결합한다.
                    if header is not None and body_start is not None:
                        first_body_box = tight_box(
                            page, left, right, body_start - 1, hard_bottom
                        )
                        body_boxes = [first_body_box] if first_body_box is not None else []
                        if marker == continuation_owner and continuation_box is not None:
                            body_boxes.append(continuation_box)

                        body_panels = [
                            trim_horizontal_whitespace(
                                trim_bottom_whitespace(remove_tip_bands(
                                    rendered.crop(pdf_box_to_pixels(page, rendered, panel_box)),
                                    page,
                                    rendered,
                                    panel_box,
                                    page_tip_regions,
                                ))
                            )
                            for panel_box in body_boxes
                        ]
                        if body_panels:
                            body = stack_panels(body_panels)
                            image = add_margin(stack_left(header, body), OUTPUT_MARGIN)
                        else:
                            image = add_margin(
                                rendered.crop(pdf_box_to_pixels(page, rendered, box)),
                                OUTPUT_MARGIN,
                            )
                    else:
                        panel_boxes = [box]
                        if marker == continuation_owner and continuation_box is not None:
                            panel_boxes.append(continuation_box)
                        panels = [
                            trim_bottom_whitespace(remove_tip_bands(
                                rendered.crop(pdf_box_to_pixels(page, rendered, panel_box)),
                                page,
                                rendered,
                                panel_box,
                                page_tip_regions,
                            ))
                            for panel_box in panel_boxes
                        ]
                        image = add_margin(stack_panels(panels), OUTPUT_MARGIN)
                    if concept_basic_page:
                        image = clean_concept_basic_image(image)

                    page_serial += 1

                    # (1), (2), ... 소문항이 있으면 "소문항 하나 = 하나의 원자 단위"로
                    # 처리한다. 전체 문제를 높이만 보고 자르면 그림이나 풀이 박스가
                    # 중간에서 끊기는 문제가 생기기 때문이다.
                    atomic_parts = []
                    if header is not None and body_start is not None:
                        atomic_boxes = _source_subquestion_boxes(
                            page, left, right, body_start - 1, hard_bottom
                        )

                        # 왼쪽 문제의 소문항이 오른쪽 단으로 이어지는 경우도 같은
                        # 방식으로 각각 원자 단위에 추가한다.
                        if marker == continuation_owner and continuation_box is not None:
                            right_left = divider + 4
                            right_right = page.width - 45
                            atomic_boxes.extend(
                                _source_subquestion_boxes(
                                    page,
                                    right_left,
                                    right_right,
                                    continuation_box[1],
                                    continuation_box[3],
                                )
                            )

                        if len(atomic_boxes) >= 2:
                            for _sub_no, atomic_box in atomic_boxes:
                                body_piece = trim_horizontal_whitespace(
                                    trim_bottom_whitespace(
                                        remove_tip_bands(
                                            rendered.crop(
                                                pdf_box_to_pixels(
                                                    page, rendered, atomic_box
                                                )
                                            ),
                                            page,
                                            rendered,
                                            atomic_box,
                                            page_tip_regions,
                                        )
                                    )
                                )
                                atomic_image = add_margin(
                                    stack_left(header, body_piece), OUTPUT_MARGIN
                                )
                                if concept_basic_page:
                                    atomic_image = clean_concept_basic_image(
                                        atomic_image
                                    )

                                # 소문항은 1200px까지 한 장을 우선한다. 그보다 긴
                                # 경우에만 분할하되, 위 split_at_whitespace가 박스의
                                # 세로 테두리 내부를 자르지 않도록 보호한다.
                                atomic_parts.extend(
                                    split_with_repeated_header(
                                        atomic_image,
                                        header,
                                        SUBQUESTION_MAX_HEIGHT,
                                    )
                                )

                    parts = (
                        atomic_parts
                        if atomic_parts
                        else split_with_repeated_header(image, header)
                    )

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
