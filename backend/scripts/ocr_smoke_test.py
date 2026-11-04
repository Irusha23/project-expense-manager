#!/usr/bin/env python3
"""Run a CPU-only PaddleOCR 3.x smoke test on one image."""

import argparse
import sys
from pathlib import Path

from ocr_service import OCRInputError, OCRService, OCRServiceError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recognize text in an image using PaddleOCR CPU inference."
    )
    parser.add_argument("image", type=Path, help="Path to the input image.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for the raw structured OCR result JSON.",
    )
    parser.add_argument(
        "--preprocess-grayscale",
        action="store_true",
        help="Convert the image to grayscale before OCR inference.",
    )
    return parser.parse_args()


def display_recognition(result) -> None:
    if not result.text_regions:
        print("No text recognized.")
        return

    print("Recognized text:")
    for region in result.text_regions:
        if region.confidence is None:
            print(f"  {region.text} (confidence unavailable)")
        else:
            print(f"  {region.text} (confidence: {region.confidence:.4f})")


def main() -> int:
    args = parse_args()
    image_path = args.image.expanduser()
    try:
        service = OCRService(preprocess_grayscale=args.preprocess_grayscale)
        result = service.process(image_path)
        output_path = service.save_raw_json(
            result,
            args.output_dir,
        )
        display_recognition(result)
        print(f"Raw OCR result saved to: {output_path}")
    except OCRInputError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except OCRServiceError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
