"""추출된 문제 이미지를 선택적으로 다크 모드로 변환한다."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def convert_to_dark_mode(
    image: Image.Image,
    background: tuple[int, int, int] = (10, 10, 10),
    foreground: tuple[int, int, int] = (245, 245, 245),
    preserve_colors: bool = True,
) -> Image.Image:
    """교재 문제 이미지의 무채색을 반전하고 유색 요소의 가독성을 높인다."""
    arr = np.array(image.convert("RGB"), dtype=np.float32)

    brightness = (
        0.299 * arr[:, :, 0]
        + 0.587 * arr[:, :, 1]
        + 0.114 * arr[:, :, 2]
    )
    color_range = arr.max(axis=2) - arr.min(axis=2)
    grayscale_mask = color_range < 30

    inverted = (255.0 - brightness) / 255.0
    for channel in range(3):
        mapped = background[channel] + inverted * (foreground[channel] - background[channel])
        arr[:, :, channel][grayscale_mask] = mapped[grayscale_mask]

    color_mask = ~grayscale_mask
    if preserve_colors:
        dark_color_mask = color_mask & (brightness < 130)
        arr[dark_color_mask] = np.clip(arr[dark_color_mask] * 1.7, 0, 255)
    else:
        arr[color_mask] = 255 - arr[color_mask]

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def convert_images_in_place(paths: list[Path]) -> list[Path]:
    """PNG 목록을 같은 파일명으로 안전하게 다크 모드 변환한다."""
    converted: list[Path] = []
    for path in sorted(paths):
        if path.suffix.lower() != ".png" or not path.is_file():
            continue
        with Image.open(path) as image:
            dark_image = convert_to_dark_mode(image, preserve_colors=True)
        temporary = path.with_name(f".{path.stem}.dark.tmp.png")
        dark_image.save(temporary, "PNG", optimize=True)
        temporary.replace(path)
        converted.append(path)
    return converted


def convert_images(paths: list[Path], output_dir: Path) -> list[Path]:
    """원본을 보존하면서 별도 폴더에 다크 모드 PNG를 만든다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    converted: list[Path] = []
    for path in sorted(paths):
        if path.suffix.lower() != ".png" or not path.is_file():
            continue
        with Image.open(path) as image:
            dark_image = convert_to_dark_mode(image, preserve_colors=True)
        output_path = output_dir / path.name
        dark_image.save(output_path, "PNG", optimize=True)
        converted.append(output_path)
    return converted
