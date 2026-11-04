"""Reusable CPU OCR service for receipt images."""

import json
import math
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TypeAlias

import cv2
import numpy as np

SUPPORTED_IMAGE_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
BoundingBox: TypeAlias = (
    list[int | float] | list[list[int | float]]
)


class OCRServiceError(RuntimeError):
    """An OCR service error that retains the original exception as its cause."""


class OCRInputError(OCRServiceError):
    """The supplied image path or image data is invalid."""


@dataclass(frozen=True)
class TextRegion:
    """One recognized text region, in PaddleOCR's original region order."""

    text: str
    confidence: float | None
    bounding_box: BoundingBox | None


@dataclass(frozen=True)
class OCRResult:
    """Normalized OCR result plus the full structured PaddleOCR response."""

    status: str
    original_input_path: str
    processing_duration_seconds: float
    text_regions: list[TextRegion]
    raw_output: list[dict[str, Any]]


def to_json_value(value: Any) -> Any:
    """Recursively convert supported NumPy and Python values into JSON values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, (Path, date, datetime)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return to_json_value(value.tolist())
    if isinstance(value, np.generic):
        return to_json_value(value.item())
    if hasattr(value, "tolist"):
        return to_json_value(value.tolist())
    raise TypeError(
        f"Unsupported value in PaddleOCR response: {type(value).__name__}"
    )


def result_payload(result: Any) -> dict[str, Any]:
    """Convert one PaddleOCR result's documented structured JSON to a dict."""
    structured = getattr(result, "json", None)
    if callable(structured):
        structured = structured()
    if not isinstance(structured, Mapping):
        raise ValueError("PaddleOCR returned no structured JSON mapping.")
    converted = to_json_value(structured)
    if not isinstance(converted, dict):
        raise ValueError("PaddleOCR structured result is not a JSON object.")
    return converted


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _bounding_box(value: Any) -> BoundingBox | None:
    if value is None:
        return None
    try:
        converted = to_json_value(value)
    except TypeError:
        return None
    if not isinstance(converted, list) or not converted:
        return None
    if all(isinstance(point, (int, float)) for point in converted):
        return converted
    if all(
        isinstance(point, list)
        and point
        and all(isinstance(coordinate, (int, float)) for coordinate in point)
        for point in converted
    ):
        return converted
    return None


def normalize_results(raw_output: list[dict[str, Any]]) -> list[TextRegion]:
    """Normalize text, aligned confidence, and box fields without reordering."""
    regions: list[TextRegion] = []
    for payload in raw_output:
        recognition = payload.get("res", payload)
        if not isinstance(recognition, Mapping):
            continue

        texts = _as_list(recognition.get("rec_texts"))
        scores = _as_list(recognition.get("rec_scores"))
        boxes = _as_list(recognition.get("rec_polys"))
        if not boxes:
            boxes = _as_list(recognition.get("dt_polys"))
        if not boxes:
            boxes = _as_list(recognition.get("rec_boxes"))

        for index, recognized_text in enumerate(texts):
            raw_score = scores[index] if index < len(scores) else None
            try:
                confidence = float(raw_score) if raw_score is not None else None
            except (TypeError, ValueError):
                confidence = None
            if confidence is not None and not math.isfinite(confidence):
                confidence = None

            box = boxes[index] if index < len(boxes) else None
            regions.append(
                TextRegion(
                    text=str(recognized_text),
                    confidence=confidence,
                    bounding_box=_bounding_box(box),
                )
            )
    return regions


class OCRService:
    """Lazily initialized, reusable PaddleOCR CPU inference service."""

    def __init__(self, *, preprocess_grayscale: bool = False) -> None:
        self.preprocess_grayscale = preprocess_grayscale
        self._engine: Any | None = None
        self._engine_lock = threading.Lock()

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is None:
                try:
                    from paddleocr import PaddleOCR

                    self._engine = PaddleOCR(
                        device="cpu",
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                    )
                except Exception as error:
                    raise OCRServiceError(
                        f"Could not initialize PaddleOCR CPU engine: {error}"
                    ) from error
        return self._engine

    def _decode_image(self, image_path: Path) -> np.ndarray:
        if image_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
            raise OCRInputError(
                f"Unsupported image extension '{image_path.suffix}'. "
                f"Supported formats: {supported}."
            )
        try:
            encoded = np.fromfile(image_path, dtype=np.uint8)
        except OSError as error:
            raise OCRInputError(
                f"Could not read input image '{image_path}': {error}"
            ) from error
        try:
            image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        except cv2.error as error:
            raise OCRInputError(
                f"Could not decode input image '{image_path}': {error}"
            ) from error
        if image is None or image.size == 0:
            raise OCRInputError(
                f"Could not decode input image '{image_path}'."
            )
        if image.dtype != np.uint8:
            raise OCRInputError(
                f"Unsupported image bit depth in '{image_path}': {image.dtype}."
            )
        if image.ndim == 2:
            return image
        if image.ndim != 3 or image.shape[2] not in (1, 3, 4):
            raise OCRInputError(
                f"Unsupported image channel layout in '{image_path}'."
            )
        return image

    def _inference_input(self, image: np.ndarray, path: Path) -> Any:
        if not self.preprocess_grayscale:
            return str(path)
        if image.ndim == 2:
            gray = image
        elif image.shape[2] == 1:
            gray = image[:, :, 0]
        elif image.shape[2] == 4:
            gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        else:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if gray.ndim == 2:
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return gray

    def process(self, image_path: str | Path) -> OCRResult:
        """Validate and recognize an image, optionally passing grayscale pixels."""
        started = time.perf_counter()
        input_path = Path(image_path).expanduser()
        try:
            resolved_path = input_path.resolve(strict=True)
        except OSError as error:
            raise OCRInputError(
                f"Input image does not exist or cannot be resolved: "
                f"'{input_path}': {error}"
            ) from error
        if not resolved_path.is_file():
            raise OCRInputError(f"Input image is not a file: '{resolved_path}'.")

        image = self._decode_image(resolved_path)
        inference_input = self._inference_input(image, resolved_path)
        engine = self._get_engine()
        try:
            predictions = engine.predict(input=inference_input)
            raw_output = [result_payload(result) for result in predictions]
        except OCRServiceError:
            raise
        except Exception as error:
            raise OCRServiceError(
                f"PaddleOCR inference failed for '{resolved_path}': {error}"
            ) from error

        return OCRResult(
            status="succeeded",
            original_input_path=str(resolved_path),
            processing_duration_seconds=time.perf_counter() - started,
            text_regions=normalize_results(raw_output),
            raw_output=raw_output,
        )

    def save_raw_json(
        self,
        result: OCRResult,
        output_dir: str | Path,
    ) -> Path:
        """Persist the complete raw PaddleOCR payload without overwriting files."""
        directory = Path(output_dir).expanduser()
        output_path = directory / "ocr_result.json"
        created = False
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with output_path.open("x", encoding="utf-8") as output_file:
                created = True
                json.dump(
                    result.raw_output,
                    output_file,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                output_file.write("\n")
        except (OSError, TypeError, ValueError) as error:
            if created:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise OCRServiceError(
                        f"Could not save raw OCR JSON to '{output_path}': {error}. "
                        f"Could not remove incomplete output: {cleanup_error}"
                    ) from error
            raise OCRServiceError(
                f"Could not save raw OCR JSON to '{output_path}': {error}"
            ) from error
        return output_path
