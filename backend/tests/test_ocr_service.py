import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import cv2
import numpy as np
import pytest

from scripts.ocr_service import (
    OCRInputError,
    OCRService,
    OCRServiceError,
)


def write_image(path: Path, *, color: tuple[int, int, int] = (20, 80, 160)) -> Path:
    image = np.full((32, 48, 3), color, dtype=np.uint8)
    encoded = cv2.imencode(path.suffix, image)[1]
    encoded.tofile(path)
    return path


def paddle_result(
    *,
    texts=None,
    scores=None,
    boxes=None,
):
    recognition = {"other_field": "preserved"}
    if texts is not None:
        recognition["rec_texts"] = texts
    if scores is not None:
        recognition["rec_scores"] = scores
    if boxes is not None:
        recognition["rec_polys"] = boxes
    return SimpleNamespace(json={"res": recognition, "top_level": "preserved"})


class FakeEngine:
    def __init__(self, predictions=None, error: Exception | None = None):
        self.predictions = predictions or []
        self.error = error
        self.inputs = []

    def predict(self, *, input):
        self.inputs.append(input)
        if self.error is not None:
            raise self.error
        return iter(self.predictions)


def install_paddle(monkeypatch, engine_factory):
    paddle_module = ModuleType("paddleocr")
    paddle_module.PaddleOCR = engine_factory
    monkeypatch.setitem(sys.modules, "paddleocr", paddle_module)


def test_engine_initializes_lazily_once_and_is_reused(tmp_path, monkeypatch):
    image_one = write_image(tmp_path / "one.png")
    image_two = write_image(tmp_path / "two.png")
    engine = FakeEngine([paddle_result(texts=["first"], scores=[0.8])])
    initializations = []

    def create_engine(**kwargs):
        initializations.append(kwargs)
        return engine

    install_paddle(monkeypatch, create_engine)
    service = OCRService()
    assert initializations == []

    first = service.process(image_one)
    second = service.process(image_two)

    assert len(initializations) == 1
    assert initializations[0] == {
        "device": "cpu",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
    }
    assert len(engine.inputs) == 2
    assert first.original_input_path == str(image_one.resolve())
    assert second.original_input_path == str(image_two.resolve())


def test_result_normalization_preserves_text_order_scores_and_boxes(
    tmp_path,
    monkeypatch,
):
    image_path = write_image(tmp_path / "receipt.png")
    raw_polygons = np.array(
        [
            [[1, 2], [10, 2], [10, 8], [1, 8]],
            [[1, 12], [20, 12], [20, 18], [1, 18]],
        ],
        dtype=np.int32,
    )
    engine = FakeEngine(
        [
            paddle_result(
                texts=["top line", "next line"],
                scores=np.array([0.91, 0.82], dtype=np.float32),
                boxes=raw_polygons,
            )
        ]
    )
    install_paddle(monkeypatch, lambda **_: engine)

    result = OCRService().process(image_path)

    assert result.status == "succeeded"
    assert result.original_input_path == str(image_path.resolve())
    assert result.processing_duration_seconds >= 0
    assert [region.text for region in result.text_regions] == ["top line", "next line"]
    assert [region.confidence for region in result.text_regions] == pytest.approx(
        [0.91, 0.82]
    )
    assert result.text_regions[0].bounding_box == raw_polygons[0].tolist()
    assert result.text_regions[1].bounding_box == raw_polygons[1].tolist()
    assert result.raw_output[0]["res"]["other_field"] == "preserved"


def test_missing_optional_confidence_and_boxes_are_none(tmp_path, monkeypatch):
    image_path = write_image(tmp_path / "receipt.png")
    engine = FakeEngine([paddle_result(texts=["text without metadata"])])
    install_paddle(monkeypatch, lambda **_: engine)

    result = OCRService().process(image_path)

    assert len(result.text_regions) == 1
    assert result.text_regions[0].confidence is None
    assert result.text_regions[0].bounding_box is None


def test_raw_json_saves_numpy_values_and_preserves_fields(tmp_path, monkeypatch):
    image_path = write_image(tmp_path / "receipt.png")
    engine = FakeEngine(
        [
            SimpleNamespace(
                json={
                    "res": {
                        "rec_texts": ["amount"],
                        "rec_scores": np.array([np.float32(0.75)]),
                        "rec_boxes": np.array([[1, 2, 3, 4]], dtype=np.int32),
                    },
                    "metadata": {"source": "PaddleOCR"},
                }
            )
        ]
    )
    install_paddle(monkeypatch, lambda **_: engine)
    service = OCRService()
    result = service.process(image_path)

    output_path = service.save_raw_json(result, tmp_path / "raw")

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved[0]["metadata"] == {"source": "PaddleOCR"}
    assert saved[0]["res"]["rec_scores"] == pytest.approx([0.75])
    assert saved[0]["res"]["rec_boxes"] == [[1, 2, 3, 4]]
    with pytest.raises(OCRServiceError, match="Could not save raw OCR JSON"):
        service.save_raw_json(result, tmp_path / "raw")


def test_invalid_image_paths_fail_before_engine_initialization(tmp_path):
    service = OCRService()
    with pytest.raises(OCRInputError, match="does not exist"):
        service.process(tmp_path / "missing.png")

    not_image = tmp_path / "invalid.png"
    not_image.write_text("not an image", encoding="utf-8")
    with pytest.raises(OCRInputError, match="Could not decode"):
        service.process(not_image)

    unsupported = tmp_path / "receipt.gif"
    unsupported.write_bytes(b"GIF89a")
    with pytest.raises(OCRInputError, match="Unsupported image extension"):
        service.process(unsupported)

    assert service._engine is None


def test_unreadable_input_directory_has_clear_error(tmp_path):
    directory = tmp_path / "folder.png"
    directory.mkdir()

    with pytest.raises(OCRInputError, match="not a file"):
        OCRService().process(directory)


def test_preprocessing_passes_grayscale_pixels_without_modifying_input(
    tmp_path,
    monkeypatch,
):
    image_path = write_image(tmp_path / "receipt.png")
    original_bytes = image_path.read_bytes()
    engine = FakeEngine([paddle_result(texts=["grayscale"])])
    install_paddle(monkeypatch, lambda **_: engine)

    result = OCRService(preprocess_grayscale=True).process(image_path)

    assert isinstance(engine.inputs[0], np.ndarray)
    assert engine.inputs[0].shape == (32, 48, 3)
    assert np.array_equal(engine.inputs[0][:, :, 0], engine.inputs[0][:, :, 1])
    assert np.array_equal(engine.inputs[0][:, :, 1], engine.inputs[0][:, :, 2])
    assert result.original_input_path == str(image_path.resolve())
    assert image_path.read_bytes() == original_bytes


def test_initialization_and_inference_failures_preserve_exception_context(
    tmp_path,
    monkeypatch,
):
    image_path = write_image(tmp_path / "receipt.png")
    initialization_error = RuntimeError("model setup failed")

    def fail_initialization(**_):
        raise initialization_error

    install_paddle(monkeypatch, fail_initialization)
    with pytest.raises(OCRServiceError, match="Could not initialize") as captured:
        OCRService().process(image_path)
    assert captured.value.__cause__ is initialization_error

    inference_error = RuntimeError("inference failed")
    engine = FakeEngine(error=inference_error)
    install_paddle(monkeypatch, lambda **_: engine)
    with pytest.raises(OCRServiceError, match="PaddleOCR inference failed") as captured:
        OCRService().process(image_path)
    assert captured.value.__cause__ is inference_error


def test_non_finite_scores_become_none(tmp_path, monkeypatch):
    image_path = write_image(tmp_path / "receipt.png")
    engine = FakeEngine(
        [paddle_result(texts=["uncertain"], scores=[np.float32(np.nan)])]
    )
    install_paddle(monkeypatch, lambda **_: engine)

    result = OCRService().process(image_path)

    assert result.text_regions[0].confidence is None
