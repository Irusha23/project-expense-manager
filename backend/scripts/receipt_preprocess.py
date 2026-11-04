#!/usr/bin/env python3
"""Generate comparable preprocessing variants for a receipt image."""

import argparse
import sys
from pathlib import Path
from typing import TypeAlias

import cv2
import numpy as np

Image: TypeAlias = np.ndarray
SUPPORTED_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
VARIANT_NAMES = (
    "01_original.png",
    "02_grayscale.png",
    "03_contrast.png",
    "04_denoised.png",
    "05_sharpened.png",
    "06_adaptive_threshold.png",
    "07_otsu_threshold.png",
    "08_deskewed.png",
)


def parse_args() -> argparse.Namespace:
    """Parse input, output, and resizing options."""
    parser = argparse.ArgumentParser(
        description="Create independent receipt image preprocessing variants."
    )
    parser.add_argument("image", type=Path, help="Path to a supported input image.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: <image-name>_preprocessed beside the image).",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=2000,
        help="Maximum output width in pixels; aspect ratio is preserved (default: 2000).",
    )
    return parser.parse_args()


def read_image(image_path: Path) -> Image:
    """Decode a supported grayscale, BGR, or BGRA uint8 image."""
    if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            "unsupported image extension; use BMP, JPEG, PNG, TIFF, or WEBP"
        )
    try:
        encoded = np.fromfile(image_path, dtype=np.uint8)
    except OSError as error:
        raise OSError(f"could not read input image: {error}") from error
    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if image is None or image.size == 0:
        raise ValueError("the file is not a readable image")
    if image.dtype != np.uint8:
        raise ValueError(f"unsupported image bit depth: {image.dtype}")
    if image.ndim == 2:
        return image
    if image.ndim != 3 or image.shape[2] not in (1, 3, 4):
        raise ValueError("image must be grayscale, BGR, or BGRA")
    return image


def to_bgr(image: Image) -> Image:
    """Convert supported grayscale or color data to three-channel BGR."""
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 1:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def resize_to_width(image: Image, max_width: int) -> Image:
    """Resize an image to a maximum width without changing its aspect ratio."""
    height, width = image.shape[:2]
    if width <= max_width:
        return image.copy()
    target_height = max(1, round(height * max_width / width))
    return cv2.resize(
        image,
        (max_width, target_height),
        interpolation=cv2.INTER_AREA,
    )


def deskew_image(gray: Image) -> tuple[Image, float | None]:
    """Correct small, reliable text skew; return None when evidence is weak."""
    binary = cv2.threshold(
        cv2.GaussianBlur(gray, (3, 3), 0),
        0,
        255,
        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
    )[1]
    height, width = binary.shape
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(12, width // 40), 1),
    )
    line_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    points = cv2.findNonZero(line_mask)
    if points is None or len(points) < 20:
        return gray.copy(), None

    raw_angle = cv2.minAreaRect(points)[-1]
    angle = -(90 + raw_angle) if raw_angle < -45 else -raw_angle
    if not 0.5 <= abs(angle) <= 12:
        return gray.copy(), None

    center = (width / 2.0, height / 2.0)
    rotation = cv2.getRotationMatrix2D(center, angle, 1.0)
    corrected = cv2.warpAffine(
        gray,
        rotation,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return corrected, angle


def order_corners(points: Image) -> Image:
    """Order a quadrilateral's points clockwise from the top-left corner."""
    corners = points.reshape(4, 2).astype(np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = corners.sum(axis=1)
    differences = np.diff(corners, axis=1).ravel()
    ordered[0] = corners[np.argmin(sums)]
    ordered[2] = corners[np.argmax(sums)]
    ordered[1] = corners[np.argmin(differences)]
    ordered[3] = corners[np.argmax(differences)]
    return ordered


def corner_angles(points: Image) -> list[float]:
    """Return the interior angles of four ordered points in degrees."""
    angles = []
    for index in range(4):
        current = points[index]
        previous = points[(index - 1) % 4] - current
        following = points[(index + 1) % 4] - current
        denominator = float(np.linalg.norm(previous) * np.linalg.norm(following))
        if denominator == 0:
            return []
        cosine = float(np.dot(previous, following) / denominator)
        angles.append(float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))))
    return angles


def correct_perspective(image: Image) -> tuple[Image | None, str]:
    """Find a plausible inset document quadrilateral and rectify it."""
    image = to_bgr(image)
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 40, 130)
    contours, _ = cv2.findContours(
        edges,
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    image_area = float(width * height)
    candidates: list[tuple[float, Image]] = []

    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        area_ratio = cv2.contourArea(polygon) / image_area
        if not 0.2 <= area_ratio <= 0.96:
            continue

        corners = order_corners(polygon)
        margin_x = width * 0.005
        margin_y = height * 0.005
        if (
            np.any(corners[:, 0] <= margin_x)
            or np.any(corners[:, 0] >= width - margin_x)
            or np.any(corners[:, 1] <= margin_y)
            or np.any(corners[:, 1] >= height - margin_y)
        ):
            continue

        angles = corner_angles(corners)
        if len(angles) != 4 or any(not 45 <= angle <= 135 for angle in angles):
            continue
        top_width = np.linalg.norm(corners[1] - corners[0])
        bottom_width = np.linalg.norm(corners[2] - corners[3])
        left_height = np.linalg.norm(corners[3] - corners[0])
        right_height = np.linalg.norm(corners[2] - corners[1])
        document_width = max(top_width, bottom_width)
        document_height = max(left_height, right_height)
        shortest_side = min(document_width, document_height)
        longest_side = max(document_width, document_height)
        if shortest_side < 0.2 * min(width, height):
            continue
        if longest_side / shortest_side > 5:
            continue

        angle_quality = 1.0 - sum(abs(angle - 90) for angle in angles) / 180
        area_quality = 1.0 - abs(area_ratio - 0.65)
        candidates.append((angle_quality + area_quality, corners))

    if not candidates:
        return None, "no plausible inset four-corner boundary"

    _, corners = max(candidates, key=lambda candidate: candidate[0])
    top_width = np.linalg.norm(corners[1] - corners[0])
    bottom_width = np.linalg.norm(corners[2] - corners[3])
    left_height = np.linalg.norm(corners[3] - corners[0])
    right_height = np.linalg.norm(corners[2] - corners[1])
    output_width = int(round(max(top_width, bottom_width)))
    output_height = int(round(max(left_height, right_height)))
    if output_width < 2 or output_height < 2:
        return None, "candidate boundary produced an unusable output size"

    destination = np.array(
        [
            [0, 0],
            [output_width - 1, 0],
            [output_width - 1, output_height - 1],
            [0, output_height - 1],
        ],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(corners, destination)
    corrected = cv2.warpPerspective(
        image,
        transform,
        (output_width, output_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return corrected, "plausible document quadrilateral detected"


def save_variant(
    output_dir: Path,
    filename: str,
    image: Image,
    original_path: Path,
) -> None:
    """Write one PNG variant or raise an informative filesystem error."""
    output_path = (output_dir / filename).resolve()
    if output_path == original_path:
        raise ValueError(
            f"refusing to overwrite the original input image: {output_path}"
        )
    try:
        saved = cv2.imwrite(str(output_path), image)
    except cv2.error as error:
        raise OSError(f"could not save {output_path.name}: {error}") from error
    if not saved:
        raise OSError(f"could not save {output_path}")
    print(f"Saved {output_path}")


def generate_variants(
    image: Image,
    output_dir: Path,
    original_path: Path,
) -> None:
    """Generate independent grayscale, enhancement, threshold, and geometry variants."""
    bgr = to_bgr(image)
    if image.ndim == 2:
        gray = image
    elif image.shape[2] == 1:
        gray = image[:, :, 0]
    elif image.shape[2] == 4:
        gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    ).apply(gray)

    save_variant(output_dir, VARIANT_NAMES[0], image, original_path)
    save_variant(output_dir, VARIANT_NAMES[1], gray, original_path)
    save_variant(output_dir, VARIANT_NAMES[2], clahe, original_path)

    denoised = cv2.bilateralFilter(bgr, d=7, sigmaColor=45, sigmaSpace=45)
    save_variant(output_dir, VARIANT_NAMES[3], denoised, original_path)

    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.2)
    sharpened = cv2.addWeighted(gray, 1.35, blurred, -0.35, 0)
    save_variant(output_dir, VARIANT_NAMES[4], sharpened, original_path)

    adaptive = cv2.adaptiveThreshold(
        clahe,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    save_variant(output_dir, VARIANT_NAMES[5], adaptive, original_path)
    otsu = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY | cv2.THRESH_OTSU,
    )[1]
    save_variant(output_dir, VARIANT_NAMES[6], otsu, original_path)

    deskewed, angle = deskew_image(gray)
    save_variant(output_dir, VARIANT_NAMES[7], deskewed, original_path)
    if angle is None:
        print("Deskew skipped: no reliable text-line skew estimate.")
    else:
        print(f"Deskew applied: {angle:.2f} degrees.")

    perspective, reason = correct_perspective(bgr)
    if perspective is None:
        print(f"Perspective correction skipped: {reason}.")
        return
    save_variant(
        output_dir,
        "09_perspective_corrected.png",
        perspective,
        original_path,
    )
    print(f"Perspective correction applied: {reason}.")


def main() -> int:
    """Validate arguments, process the image, and report failures with exit codes."""
    args = parse_args()
    input_path = args.image.expanduser()
    if args.max_width < 1:
        print("Error: --max-width must be a positive integer.", file=sys.stderr)
        return 2
    if not input_path.exists() or not input_path.is_file():
        print(f"Error: input image is not a file: {input_path}", file=sys.stderr)
        return 2
    try:
        original_path = input_path.resolve(strict=True)
    except OSError as error:
        print(f"Error: could not resolve input image: {error}", file=sys.stderr)
        return 2

    output_dir = args.output_dir or input_path.with_name(
        f"{input_path.stem}_preprocessed"
    )
    output_dir = output_dir.expanduser()
    try:
        if output_dir.resolve() == original_path:
            print("Error: output directory cannot be the input file.", file=sys.stderr)
            return 2
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"Error: could not create output directory: {error}", file=sys.stderr)
        return 2

    try:
        image = resize_to_width(read_image(original_path), args.max_width)
        generate_variants(image, output_dir, original_path)
    except (OSError, ValueError, cv2.error) as error:
        print(f"Error: receipt preprocessing failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
