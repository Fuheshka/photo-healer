# -*- coding: utf-8 -*-
"""Interactive Split-Preview widget for Photo Healer.

Provides side-by-side / split slider comparison between "Before" (damaged file
or carved preview) and "After" (on-the-fly reconstructed JPEG), with synchronized
zooming (mouse wheel anchored) and panning (drag with LMB), in-memory caching,
and high-contrast forensic status overlays.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from photo_healer.core.carver import ThumbnailCarver
from photo_healer.core.entropy import EntropyAnalyzer
from photo_healer.core.parser import JpegParser
from photo_healer.core.resync import StreamResync
from photo_healer.core.splicer import HeaderSplicer
from photo_healer.core.validator import JpegValidator
from photo_healer.gui.i18n import i18n, t


def pil_to_qimage(pil_img: Image.Image) -> QImage:
    """Convert a Pillow Image to a QImage safely with proper stride."""
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    data = pil_img.tobytes("raw", "RGB")
    bytes_per_line = pil_img.width * 3
    qimg = QImage(data, pil_img.width, pil_img.height, bytes_per_line, QImage.Format.Format_RGB888)
    return qimg.copy()


class SplitPreviewWidget(QWidget):
    """Interactive Before / After split comparison canvas with zoom & pan."""

    zoom_changed = Signal(float)
    view_mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Image references and metadata
        self._before_image: QImage | None = None
        self._after_image: QImage | None = None
        self._before_label: str = t("heal.preview.before")
        self._after_label: str = t("heal.preview.after")
        self._before_info: str = ""
        self._after_info: str = ""
        self._is_diagnostic_pattern: bool = False

        # View configuration
        self._view_mode: str = "split"  # "split", "before", "after"
        self._split_ratio: float = 0.5  # 0.02 to 0.98

        # Zoom and Pan coordinates
        self._scale_factor: float = 1.0
        self._pan_offset: QPointF = QPointF(0.0, 0.0)
        self._fit_mode: bool = True

        # Mouse interaction states
        self._is_dragging_split: bool = False
        self._is_panning: bool = False
        self._last_mouse_pos: QPointF = QPointF(0.0, 0.0)
        self._split_handle_hovered: bool = False

        # In-memory image cache: key -> QImage
        self._cache: dict[str, QImage] = {}

        # Connect live reactive localization
        i18n.language_changed.connect(self._on_language_changed)

    def _on_language_changed(self) -> None:
        """Update label defaults on language change if using standard labels."""
        self._before_label = t("heal.preview.before")
        self._after_label = t("heal.preview.after")
        self.update()

    def clear(self) -> None:
        """Clear loaded images and reset coordinates."""
        self._before_image = None
        self._after_image = None
        self._before_info = ""
        self._after_info = ""
        self._is_diagnostic_pattern = False
        self._pan_offset = QPointF(0.0, 0.0)
        self._scale_factor = 1.0
        self._fit_mode = True
        self.update()

    def clear_cache(self) -> None:
        """Purge in-memory rendered image cache."""
        self._cache.clear()

    def set_images(
        self,
        before_image: QImage | None,
        after_image: QImage | None,
        before_info: str = "",
        after_info: str = "",
        is_diagnostic: bool = False,
    ) -> None:
        """Explicitly set Before and After images with info strings."""
        self._before_image = before_image
        self._after_image = after_image
        self._before_info = before_info
        self._after_info = after_info
        self._is_diagnostic_pattern = is_diagnostic
        self._fit_mode = True
        self.fit_to_view()
        self.update()

    def load_comparison(
        self,
        candidate_path: Path | str,
        donor_path: Path | str | None = None,
        pad_geometry: bool = False,
    ) -> tuple[bool, str]:
        """Perform on-the-fly reconstruction and render Before/After comparison.

        Args:
            candidate_path: Damaged file path.
            donor_path: Donor file path or None if no donor is available yet.
            pad_geometry: Whether to insert dummy MCU restart intervals via StreamResync.

        Returns:
            (success, status_description)
        """
        cand_p = Path(candidate_path)
        if not cand_p.is_file():
            self.clear()
            return False, f"File not found: {cand_p}"

        cache_key = f"{cand_p.resolve()}_{cand_p.stat().st_mtime}_{donor_path}_{pad_geometry}"

        # ── 1. Resolve Before Image ───────────────────────────────────────────
        before_img: QImage | None = None
        before_info: str = ""
        is_diagnostic = False

        # First, try direct decode if it happens to be valid
        cand_bytes = cand_p.read_bytes()
        try:
            pil_before = Image.open(io.BytesIO(cand_bytes))
            pil_before.load()
            before_img = pil_to_qimage(pil_before)
            before_info = f"{pil_before.width}x{pil_before.height} px (Direct)"
        except Exception:
            # Cannot decode directly; carve best thumbnail / preview
            carved = ThumbnailCarver.extract_best_preview(cand_bytes)
            if carved is not None:
                try:
                    pil_thumb = Image.open(io.BytesIO(carved.data))
                    pil_thumb.load()
                    before_img = pil_to_qimage(pil_thumb)
                    before_info = f"{pil_thumb.width}x{pil_thumb.height} px ({carved.preview_type.upper()})"
                except Exception:
                    before_img = None

        if before_img is None:
            # TRIM zeroed header without carved preview -> generate forensic pattern
            is_diagnostic = True
            first_nz = next((idx for idx, b in enumerate(cand_bytes) if b != 0), -1)
            offset_str = f"{first_nz} B" if first_nz > 0 else "100% 0x00"
            before_info = t("heal.preview.trim_zero_notice", offset=offset_str, default=f"TRIM Zeroed Header ({offset_str})")
            before_img = self._generate_diagnostic_card(cand_p.name, cand_bytes, first_nz)

        # ── 2. Resolve After Image ────────────────────────────────────────────
        after_img: QImage | None = None
        after_info: str = ""
        reconstruct_success = False

        if donor_path:
            donor_p = Path(donor_path)
            if donor_p.is_file():
                if cache_key in self._cache:
                    after_img = self._cache[cache_key]
                    after_info = f"{after_img.width()}x{after_img.height()} px (Cached)"
                    reconstruct_success = True
                else:
                    try:
                        donor_bytes = donor_p.read_bytes()
                        reconstructed_bytes: bytes | None = None
                        method_used = "splice"

                        # Check if restart markers exist
                        markers = StreamResync.scan_restart_markers(cand_bytes)
                        if markers and len(markers) >= 2:
                            # StreamResync path
                            resync_res = StreamResync.resync_stream(
                                cand_bytes,
                                donor_bytes,
                                pad_geometry=pad_geometry,
                            )
                            if resync_res.status == "resynced" or resync_res.total_markers_found > 0:
                                reconstructed_bytes = resync_res.data
                                method_used = "resync (padded)" if pad_geometry else "resync"

                        if reconstructed_bytes is None:
                            # Standard header splice path
                            donor_hdr = JpegParser(donor_bytes).get_header_bytes()
                            detected_offset = EntropyAnalyzer.detect_entropy_start(cand_bytes)
                            if detected_offset is None:
                                detected_offset = next((i for i, b in enumerate(cand_bytes) if b != 0), 65536)
                            splicer = HeaderSplicer(donor_hdr)
                            splice_res = splicer.splice_target(cand_bytes, entropy_offset=detected_offset)
                            reconstructed_bytes = splice_res.data
                            method_used = f"splice (offset {splice_res.entropy_offset})"

                        # Load into QImage / Pillow
                        qimg = QImage()
                        if qimg.loadFromData(reconstructed_bytes, "JPEG"):
                            after_img = qimg
                            self._cache[cache_key] = after_img
                            after_info = f"{after_img.width()}x{after_img.height()} px ({method_used})"
                            reconstruct_success = True
                        else:
                            try:
                                from PIL import ImageFile
                                ImageFile.LOAD_TRUNCATED_IMAGES = True
                                pil_after = Image.open(io.BytesIO(reconstructed_bytes))
                                pil_after.load()
                                after_img = pil_to_qimage(pil_after)
                                self._cache[cache_key] = after_img
                                after_info = f"{after_img.width()}x{after_img.height()} px ({method_used})"
                                reconstruct_success = True
                            except Exception as pil_err:
                                val = JpegValidator.validate(reconstructed_bytes)
                                if val.is_valid and val.width and val.height:
                                    after_img = self._generate_reconstructed_card(
                                        cand_p.name, val.width, val.height, method_used
                                    )
                                    self._cache[cache_key] = after_img
                                    after_info = f"{val.width}x{val.height} px ({method_used})"
                                    reconstruct_success = True
                                else:
                                    after_info = t(
                                        "heal.preview.decoding_error",
                                        error=str(pil_err),
                                        default=f"Reconstruction error: {pil_err}",
                                    )
                    except Exception as e:
                        after_info = t("heal.preview.decoding_error", error=str(e), default=f"Reconstruction error: {e}")
            else:
                after_info = t("heal.donor.none", default="Donor file not found")
        else:
            after_info = t("heal.donor.none", default="No donor selected")

        self.set_images(
            before_image=before_img,
            after_image=after_img,
            before_info=before_info,
            after_info=after_info,
            is_diagnostic=is_diagnostic,
        )

        return reconstruct_success, after_info

    def _generate_diagnostic_card(self, filename: str, cand_bytes: bytes, first_nz: int) -> QImage:
        """Create a synthetic high-contrast forensic card when header is erased."""
        w, h = 1280, 960
        img = QImage(w, h, QImage.Format.Format_RGB888)
        img.fill(QColor("#18181b"))

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw grid background
        grid_pen = QPen(QColor("#27272a"), 1, Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)
        step = 40
        for x in range(0, w, step):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            painter.drawLine(0, y, w, y)

        # Central Card
        card_rect = QRectF(240, 240, 800, 480)
        painter.setBrush(QBrush(QColor("#202024")))
        painter.setPen(QPen(QColor("#ef4444"), 2))
        painter.drawRoundedRect(card_rect, 12, 12)

        # Card Title
        painter.setPen(QColor("#f43f5e"))
        font = QFont("Segoe UI", 20, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(card_rect.adjusted(30, 30, -30, -380), Qt.AlignmentFlag.AlignLeft, "TRIM ZEROED HEADER")

        # Details
        painter.setPen(QColor("#f4f4f5"))
        font_body = QFont("Consolas", 13)
        painter.setFont(font_body)

        nz_text = f"Offset {first_nz} (0x{first_nz:05X})" if first_nz > 0 else "All Zeros (Unrecoverable)"
        lines = [
            f"File Target   : {filename}",
            f"File Size     : {len(cand_bytes):,} bytes",
            f"Header Status : Erased by SSD TRIM (0x00 sectors)",
            f"Entropy Start : {nz_text}",
            f"Live Payload  : {max(0, len(cand_bytes) - max(0, first_nz)):,} bytes",
            "",
            "Exif thumbnail is missing due to sector wipe.",
            "Use the 'After' pane to preview donor reconstruction.",
        ]

        text_y = 330
        for line in lines:
            painter.drawText(270, text_y, line)
            text_y += 32

        painter.end()
        return img

    def _generate_reconstructed_card(self, filename: str, w: int, h: int, method: str) -> QImage:
        """Render a synthetic forensic preview when scanlines are partially synthetic."""
        img = QImage(w, h, QImage.Format.Format_RGB888)
        img.fill(QColor("#18181b"))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Subtle grid
        painter.setPen(QPen(QColor("#27272a"), 1, Qt.PenStyle.DotLine))
        step = max(16, min(w, h) // 10)
        for x in range(0, w, step):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            painter.drawLine(0, y, w, y)

        # Central Box
        card = QRectF(10, 10, max(10.0, float(w - 20)), max(10.0, float(h - 20)))
        painter.setBrush(QBrush(QColor("#202024")))
        painter.setPen(QPen(QColor("#10b981"), 1.5))
        painter.drawRoundedRect(card, 8, 8)

        # Title
        painter.setPen(QColor("#10b981"))
        f_size = max(8, min(16, h // 12))
        painter.setFont(QFont("Segoe UI", f_size, QFont.Weight.Bold))
        painter.drawText(card.adjusted(12, 10, -12, -10), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, "RECONSTRUCTED JPEG")

        # Info
        painter.setPen(QColor("#f4f4f5"))
        painter.setFont(QFont("Consolas", max(7, min(12, h // 16))))
        info_text = f"Target: {filename}\nResolution: {w}x{h} px\nMethod: {method}"
        painter.drawText(card.adjusted(12, 14 + f_size * 2, -12, -10), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, info_text)

        painter.end()
        return img

    def _get_target_size(self) -> QSize:
        """Returns reference dimensions based on available images."""
        if self._after_image is not None:
            return self._after_image.size()
        if self._before_image is not None:
            return self._before_image.size()
        return QSize(800, 600)

    def _calc_fit_scale(self) -> float:
        """Calculate scale factor so reference image fits within widget bounds."""
        ref_size = self._get_target_size()
        if ref_size.width() <= 0 or ref_size.height() <= 0:
            return 1.0

        margin = 32
        avail_w = max(10, self.width() - margin)
        avail_h = max(10, self.height() - margin)

        scale_w = avail_w / ref_size.width()
        scale_h = avail_h / ref_size.height()
        return min(scale_w, scale_h, 3.0)

    def fit_to_view(self) -> None:
        """Reset view to fit images inside current widget dimensions."""
        self._scale_factor = self._calc_fit_scale()
        self._pan_offset = QPointF(0.0, 0.0)
        self._fit_mode = True
        self.zoom_changed.emit(self._scale_factor)
        self.update()

    def reset_zoom(self) -> None:
        """Reset zoom to 100% (1:1 actual pixels)."""
        self._scale_factor = 1.0
        self._pan_offset = QPointF(0.0, 0.0)
        self._fit_mode = False
        self.zoom_changed.emit(self._scale_factor)
        self.update()

    def zoom_in(self, factor: float = 1.25) -> None:
        """Zoom in by factor."""
        self._scale_factor = min(20.0, self._scale_factor * factor)
        self._fit_mode = False
        self.zoom_changed.emit(self._scale_factor)
        self.update()

    def zoom_out(self, factor: float = 0.8) -> None:
        """Zoom out by factor."""
        self._scale_factor = max(0.05, self._scale_factor * factor)
        self._fit_mode = False
        self.zoom_changed.emit(self._scale_factor)
        self.update()

    def set_split_ratio(self, ratio: float) -> None:
        """Set divider position ratio (clamped 0.02..0.98)."""
        self._split_ratio = max(0.02, min(0.98, ratio))
        self.update()

    def set_view_mode(self, mode: str) -> None:
        """Set view mode: 'split', 'before', or 'after'."""
        if mode in {"split", "before", "after"}:
            self._view_mode = mode
            self.view_mode_changed.emit(mode)
            self.update()

    def get_view_mode(self) -> str:
        return self._view_mode

    def get_scale_factor(self) -> float:
        return self._scale_factor

    # ── Paint Event ───────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        width = self.width()
        height = self.height()

        # 1. Canvas Background
        painter.fillRect(0, 0, width, height, QColor("#121214"))

        # Empty state prompt
        if self._before_image is None and self._after_image is None:
            self._draw_empty_state(painter)
            return

        ref_size = self._get_target_size()
        target_w = ref_size.width() * self._scale_factor
        target_h = ref_size.height() * self._scale_factor

        center_x = (width / 2.0) + self._pan_offset.x()
        center_y = (height / 2.0) + self._pan_offset.y()

        img_rect = QRectF(
            center_x - (target_w / 2.0),
            center_y - (target_h / 2.0),
            target_w,
            target_h,
        )

        split_x = width * self._split_ratio

        # 2. Draw Images according to view mode
        if self._view_mode == "before":
            if self._before_image is not None:
                self._draw_image(painter, self._before_image, img_rect)
        elif self._view_mode == "after":
            if self._after_image is not None:
                self._draw_image(painter, self._after_image, img_rect)
            else:
                self._draw_no_after_placeholder(painter, img_rect)
        else:
            # Mode "split"
            # Left clip: Before
            painter.save()
            painter.setClipRect(QRectF(0, 0, split_x, height))
            if self._before_image is not None:
                self._draw_image(painter, self._before_image, img_rect)
            painter.restore()

            # Right clip: After
            painter.save()
            painter.setClipRect(QRectF(split_x, 0, width - split_x, height))
            if self._after_image is not None:
                self._draw_image(painter, self._after_image, img_rect)
            else:
                self._draw_no_after_placeholder(painter, img_rect)
            painter.restore()

            # Draw Split Divider Line & Handle
            self._draw_split_divider(painter, split_x, height)

        # 3. Badges and Controls Overlay
        self._draw_overlay_badges(painter, split_x)

    def _draw_image(self, painter: QPainter, img: QImage, target_rect: QRectF) -> None:
        """Draw scaled image with subtle 1px border."""
        painter.drawImage(target_rect, img)
        painter.setPen(QPen(QColor(255, 255, 255, 25), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(target_rect)

    def _draw_split_divider(self, painter: QPainter, split_x: float, height: int) -> None:
        """Draw interactive vertical line and central grip handle."""
        # Vertical divider line
        divider_pen = QPen(QColor("#3b82f6"), 2)
        painter.setPen(divider_pen)
        painter.drawLine(int(split_x), 0, int(split_x), height)

        # Central draggable pill handle
        handle_w = 34
        handle_h = 48
        handle_y = (height / 2.0) - (handle_h / 2.0)
        handle_rect = QRectF(split_x - (handle_w / 2.0), handle_y, handle_w, handle_h)

        handle_bg = QColor("#2563eb") if self._split_handle_hovered or self._is_dragging_split else QColor("#1e293b")
        handle_border = QColor("#60a5fa") if self._split_handle_hovered or self._is_dragging_split else QColor("#475569")

        painter.setBrush(QBrush(handle_bg))
        painter.setPen(QPen(handle_border, 1.5))
        painter.drawRoundedRect(handle_rect, 6, 6)

        # Grip arrows (◂ ▸)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        painter.drawText(handle_rect, Qt.AlignmentFlag.AlignCenter, "◂ ▸")

    def _draw_no_after_placeholder(self, painter: QPainter, img_rect: QRectF) -> None:
        """Draw placeholder on After side when no donor is loaded yet."""
        painter.fillRect(img_rect, QColor("#18181b"))
        painter.setPen(QPen(QColor("#3f3f46"), 1, Qt.PenStyle.DashLine))
        painter.drawRect(img_rect)

        painter.setPen(QColor("#a1a1aa"))
        font = QFont("Segoe UI", 13)
        painter.setFont(font)
        painter.drawText(img_rect, Qt.AlignmentFlag.AlignCenter, t("heal.donor.none", default="No donor loaded\nSelect a donor file to preview"))

    def _draw_empty_state(self, painter: QPainter) -> None:
        """Draw friendly empty state when no candidate is selected."""
        rect = self.rect()
        painter.setPen(QColor("#71717a"))
        font = QFont("Segoe UI", 14)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, t("heal.preview.no_selection", default="Select a candidate from the queue to preview"))

    def _draw_overlay_badges(self, painter: QPainter, split_x: float) -> None:
        """Draw sleek Better-UI floating label cards at top corners."""
        # 1. "Before" Pill (Top-Left)
        if self._view_mode in {"split", "before"}:
            before_text = f"{self._before_label}"
            if self._before_info:
                before_text += f" • {self._before_info}"
            self._draw_pill(painter, 16, 16, before_text, QColor("#ef4444"), is_left=True)

        # 2. "After" Pill (Top-Right)
        if self._view_mode in {"split", "after"}:
            after_text = f"{self._after_label}"
            if self._after_info:
                after_text += f" • {self._after_info}"
            self._draw_pill(painter, self.width() - 16, 16, after_text, QColor("#10b981"), is_left=False)

        # 3. Zoom level pill (Bottom-Right)
        zoom_pct = int(self._scale_factor * 100)
        mode_text = "Fit" if self._fit_mode else f"{zoom_pct}%"
        zoom_label = f"Zoom: {mode_text}"
        self._draw_pill(painter, self.width() - 16, self.height() - 36, zoom_label, QColor("#60a5fa"), is_left=False)

    def _draw_pill(
        self,
        painter: QPainter,
        x: int,
        y: int,
        text: str,
        dot_color: QColor,
        is_left: bool = True,
    ) -> None:
        """Draw a frosted pill badge."""
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Medium))
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text)
        pill_w = text_w + 32
        pill_h = 28

        pill_x = x if is_left else x - pill_w
        pill_rect = QRectF(pill_x, y, pill_w, pill_h)

        # Frosted dark background
        painter.setBrush(QBrush(QColor(24, 24, 27, 220)))
        painter.setPen(QPen(QColor(63, 63, 70, 200), 1))
        painter.drawRoundedRect(pill_rect, 6, 6)

        # Status dot
        dot_x = pill_x + 10
        dot_y = y + (pill_h / 2.0)
        painter.setBrush(QBrush(dot_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(dot_x, dot_y), 4, 4)

        # Text
        painter.setPen(QColor("#f4f4f5"))
        text_rect = QRectF(dot_x + 8, y, text_w + 10, pill_h)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    # ── Mouse Interaction (Zoom & Pan & Split Drag) ───────────────────────────

    def _is_over_split_handle(self, pos: QPointF) -> bool:
        """Check if mouse position is within the split divider handle zone."""
        split_x = self.width() * self._split_ratio
        return abs(pos.x() - split_x) <= 12

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            if self._view_mode == "split" and self._is_over_split_handle(pos):
                self._is_dragging_split = True
                self.setCursor(Qt.CursorShape.SplitHCursor)
            else:
                self._is_panning = True
                self._last_mouse_pos = pos
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()

        if self._is_dragging_split:
            new_ratio = pos.x() / max(1.0, float(self.width()))
            self.set_split_ratio(new_ratio)
            return

        if self._is_panning:
            delta = pos - self._last_mouse_pos
            self._pan_offset += delta
            self._last_mouse_pos = pos
            self._fit_mode = False
            self.update()
            return

        # Cursor and Hover updates
        if self._view_mode == "split" and self._is_over_split_handle(pos):
            if not self._split_handle_hovered:
                self._split_handle_hovered = True
                self.setCursor(Qt.CursorShape.SplitHCursor)
                self.update()
        else:
            if self._split_handle_hovered:
                self._split_handle_hovered = False
                self.update()
            if not self._is_panning:
                self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton}:
            self._is_dragging_split = False
            self._is_panning = False
            if self._view_mode == "split" and self._is_over_split_handle(event.position()):
                self.setCursor(Qt.CursorShape.SplitHCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Anchored zooming with mouse wheel."""
        delta = event.angleDelta().y()
        if delta == 0:
            return

        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        old_scale = self._scale_factor
        new_scale = max(0.05, min(20.0, old_scale * factor))

        if new_scale != old_scale:
            # Zoom anchored to mouse cursor
            mouse_pos = event.position()
            widget_center = QPointF(self.width() / 2.0, self.height() / 2.0)
            cursor_offset = mouse_pos - widget_center - self._pan_offset

            # Adjust pan offset so point under cursor stays invariant
            scale_ratio = new_scale / old_scale
            self._pan_offset = self._pan_offset - (cursor_offset * (scale_ratio - 1.0))
            self._scale_factor = new_scale
            self._fit_mode = False
            self.zoom_changed.emit(self._scale_factor)
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Double click resets zoom and re-fits images to view."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.fit_to_view()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self._scale_factor = self._calc_fit_scale()
