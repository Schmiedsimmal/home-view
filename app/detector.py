"""YOLOv8 object detection (COCO pretrained) via ONNX Runtime.

The Jetson base image ships OpenCV 4.5, whose ``cv2.dnn`` ONNX importer
cannot parse YOLOv8 graphs (shape assertion / unsupported nodes). Instead
we run inference with ``onnxruntime`` (aarch64 wheel, CPU provider) on the
exported ``yolov8n.onnx``.

Model file: an exported ``yolov8n.onnx`` (e.g. produced with
``yolo export model=yolov8n.pt format=onnx imgsz=640``) placed in the
``/app/models`` volume (bind-mounted from ``./models`` on the host).

Preprocessing uses letterbox resize (aspect-ratio preserving) to the
configured ``img_size``; boxes are decoded from the YOLOv8 output tensor
``[1, 4+nc, anchors]`` and filtered with ``cv2.dnn.NMSBoxes`` (CPU-side,
works fine on OpenCV 4.5).
"""
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import DetectorConfig

log = logging.getLogger(__name__)

MODELS_DIR = "/app/models"

# COCO class names (80 classes, ultralytics default).
COCO_NAMES: List[str] = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon",
    "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

# BGR colors for common home-monitoring classes.
CLASS_COLORS: Dict[int, Tuple[int, int, int]] = {
    0: (0, 0, 255),    # person -> red
    15: (0, 255, 255), # cat -> yellow
    16: (0, 255, 0),   # dog -> green
}
DEFAULT_COLOR = (200, 200, 200)


def _letterbox(img, new_size: int):
    """Resize *img* to fit *new_size* keeping aspect ratio, pad to square.

    Returns ``(letterboxed_img, scale, (pad_w, pad_h))`` where *scale* maps
    original pixels to the padded image and *pad* is the top-left offset.
    """
    h, w = img.shape[:2]
    scale = min(new_size / h, new_size / w)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_w = (new_size - new_w) // 2
    pad_h = (new_size - new_h) // 2
    out = cv2.copyMakeBorder(
        resized, pad_h, new_size - new_h - pad_h, pad_w, new_size - new_w - pad_w,
        cv2.BORDER_CONSTANT, value=(114, 114, 114),
    )
    return out, scale, (pad_w, pad_h)


class Detector:
    """Wrapper around onnxruntime for ONNX-exported YOLOv8 models."""

    def __init__(self, cfg: DetectorConfig, models_dir: str = MODELS_DIR) -> None:
        self.cfg = cfg
        self.models_dir = models_dir
        os.makedirs(models_dir, exist_ok=True)
        self._sess: Any = None
        self._input_name: str = ""
        self._device: str = "cpu"
        # COCO class subset actually searched (None = all 80).
        self._classes: Optional[List[int]] = None

    # -- model path -------------------------------------------------------------
    def _model_path(self) -> str:
        """Resolve cfg.model to an existing ONNX file."""
        candidates = []
        if os.path.isabs(self.cfg.model):
            candidates.append(self.cfg.model)
        else:
            candidates += [
                os.path.join(self.models_dir, self.cfg.model),
                os.path.join(os.getcwd(), self.cfg.model),
                os.path.join("/app", self.cfg.model),
            ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return candidates[0]

    # -- load -------------------------------------------------------------------
    def load(self) -> None:
        try:
            import onnxruntime as ort
        except ImportError:
            raise RuntimeError(
                "onnxruntime nicht installiert (pip install onnxruntime==1.10.0)"
            )

        path = self._model_path()
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"YOLO ONNX model not found: {path} "
                f"(put {self.cfg.model} into {self.models_dir})"
            )
        log.info("Loading YOLO ONNX model: %s", path)

        # CPU-only: onnxruntime 1.10 aarch64 hat keinen CUDA-Provider.
        self._device = "cpu"
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self._sess = ort.InferenceSession(
            path, sess_options=opts, providers=["CPUExecutionProvider"],
        )
        self._input_name = self._sess.get_inputs()[0].name
        self._classes = self.cfg.classes or None
        log.info("Model ready (device=cpu, classes=%s).", self._classes)

    # -- properties ---------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._sess is not None

    @property
    def using_engine(self) -> bool:
        """Kept for API compatibility."""
        return False

    @property
    def device(self) -> str:
        return self._device

    # -- inference ------------------------------------------------------------------
    def _preprocess(self, frame):
        lb, scale, (pad_w, pad_h) = _letterbox(frame, self.cfg.img_size)
        # blobFromImage: scale 1/255, swapRB (BGR->RGB), CHW, float32.
        blob = cv2.dnn.blobFromImage(lb, scalefactor=1.0 / 255.0, swapRB=True)
        return blob, scale, pad_w, pad_h

    def _postprocess(self, outputs, frame_shape, scale, pad_w, pad_h):
        """Decode YOLOv8 output ``[1, 4+nc, anchors]`` into detections."""
        h_img, w_img = frame_shape[:2]
        out = np.asarray(outputs[0])
        if out.ndim == 3:
            out = out[0]
        # Expect [channels, anchors] (4+nc rows); transpose to [anchors, channels].
        if out.shape[0] < out.shape[1]:
            out = out.T

        boxes_c = out[:, :4].astype(np.float32)   # cx, cy, w, h (letterbox px)
        scores = out[:, 4:].astype(np.float32)    # [anchors, nc]

        if self._classes:
            scores = scores[:, self._classes]
        cls_local = np.argmax(scores, axis=1)
        conf = scores[np.arange(scores.shape[0]), cls_local]
        keep = conf >= self.cfg.conf_threshold
        if not np.any(keep):
            return []

        idx = np.nonzero(keep)[0]
        boxes_c = boxes_c[idx]
        conf = conf[idx]
        if self._classes:
            cls_ids = [self._classes[int(i)] for i in cls_local[idx]]
        else:
            cls_ids = [int(i) for i in cls_local[idx]]

        # cx,cy,w,h (letterbox) -> x1,y1,x2,y2 (original frame coords)
        cx, cy, bw, bh = boxes_c.T
        x1 = (cx - bw / 2.0 - pad_w) / scale
        y1 = (cy - bh / 2.0 - pad_h) / scale
        x2 = (cx + bw / 2.0 - pad_w) / scale
        y2 = (cy + bh / 2.0 - pad_h) / scale
        x1 = np.clip(x1, 0, w_img)
        y1 = np.clip(y1, 0, h_img)
        x2 = np.clip(x2, 0, w_img)
        y2 = np.clip(y2, 0, h_img)

        # NMS expects [x, y, w, h].
        nms_boxes = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).tolist()
        nms_idx = cv2.dnn.NMSBoxes(
            nms_boxes, conf.tolist(), self.cfg.conf_threshold, self.cfg.nms_threshold,
        )
        if len(nms_idx) == 0:
            return []
        nms_idx = np.asarray(nms_idx).flatten()

        detections = []
        for i in nms_idx:
            i = int(i)
            cls_id = cls_ids[i]
            detections.append({
                "class_id": cls_id,
                "label": COCO_NAMES[cls_id] if cls_id < len(COCO_NAMES) else str(cls_id),
                "conf": float(conf[i]),
                "box": (int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i])),
            })
        return detections

    def detect(self, frame, draw: bool = False):
        """Run inference; return ``(frame, detections)``.

        *detections* is a list of dicts: ``class_id``, ``label``, ``conf``,
        ``box`` (x1, y1, x2, y2). If *draw* is True, boxes are drawn on *frame*.
        """
        if self._sess is None:
            return frame, []

        blob, scale, pad_w, pad_h = self._preprocess(frame)
        outputs = self._sess.run(None, {self._input_name: blob})
        detections = self._postprocess(outputs, frame.shape, scale, pad_w, pad_h)

        if draw:
            for det in detections:
                x1, y1, x2, y2 = det["box"]
                self._draw_box(frame, x1, y1, x2, y2, det["class_id"], det["conf"])
        return frame, detections

    @staticmethod
    def _draw_box(frame, x1, y1, x2, y2, cls_id, conf) -> None:
        color = CLASS_COLORS.get(cls_id, DEFAULT_COLOR)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{COCO_NAMES[cls_id] if cls_id < len(COCO_NAMES) else cls_id} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, max(0, y1 - th - 4)), (x1 + tw + 4, y1), color, -1)
        cv2.putText(
            frame, label, (x1 + 2, max(th, y1 - 2)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
        )
