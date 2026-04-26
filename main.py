import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import cv2
from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl


# ------------------------------
# 基础配置常量
# ------------------------------
WINDOW_WIDTH = 600
WINDOW_HEIGHT = 980
PREVIEW_SIZE = 520

COLOR_BG = "#FFF8F0"
COLOR_MAIN_BLUE = "#89CFF0"
COLOR_MAIN_PINK = "#F4C2C2"

# 自动预览逻辑常量
AUTO_PREVIEW_DELAY_MS = 500
AUTO_PREVIEW_DURATION_SEC = 2.9
DEFAULT_PREVIEW_FPS = 25.0
PREVIEW_MAX_EDGE = 500

# Telegram WebM 转换硬性指标
TARGET_DURATION_SEC = 2.9
TARGET_MAX_BYTES = 255 * 1024
INITIAL_BITRATE_KBPS = 600
MIN_BITRATE_KBPS = 40
MAX_ENCODE_ATTEMPTS = 10
TARGET_FPS = 29.975

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"
}

SUBPROCESS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def resource_path(*parts: str) -> str:
    """
    获取资源路径。
    这里兼容开发环境与 PyInstaller 打包后的运行环境。
    """
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base_dir.joinpath(*parts))


def runtime_base_path() -> str:
    """
    获取程序运行时真正应该参考的基础目录。

    说明：
    1. 开发环境下，返回当前 main.py 所在目录。
    2. 打包成 EXE 后，返回 EXE 文件自身所在目录。

    这样在绿色版目录中寻找 bin/ffmpeg.exe 时，就不会误指向 PyInstaller 的内部目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_first_existing_path(candidates: list[str]) -> str | None:
    """返回第一个真实存在的路径。"""
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def is_supported_video(path: str) -> bool:
    """判断当前路径是否为受支持的视频文件。"""
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS


def format_seconds(seconds: float) -> str:
    """
    将秒数格式化为 mm:ss.xx。
    这样时间标签更适合放在进度条上方。
    """
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    remain = seconds - minutes * 60
    return f"{minutes:02d}:{remain:05.2f}"


def elide_middle(text: str, max_chars: int = 42) -> str:
    """对过长路径做中间省略，避免撑坏界面。"""
    if len(text) <= max_chars:
        return text
    keep = max(8, (max_chars - 3) // 2)
    return f"{text[:keep]}...{text[-keep:]}"


def status_chip_style(font_size: int = 12) -> str:
    """统一的信息标签圆角背景样式。"""
    return (
        "QLabel {"
        "background-color: rgba(255,255,255,0.92);"
        "border: 1px solid #F0E4D7;"
        "border-radius: 14px;"
        "padding: 8px 12px;"
        "color: #7A7A7A;"
        f"font-size: {font_size}px;"
        "}"
    )


class RoundedMessageDialog(QDialog):
    """统一风格的圆角提示框。"""

    def __init__(
        self,
        title: str,
        message: str,
        parent: QWidget | None = None,
        folder_path: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(360, 200)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)

        card = QFrame()
        card.setStyleSheet(
            f"""
            QFrame {{
                background-color: {COLOR_BG};
                border-radius: 24px;
                border: 1px solid #F0E4D7;
            }}
            """
        )

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 8)
        card.setGraphicsEffect(shadow)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #555;")
        layout.addWidget(title_label)

        content_label = QLabel(message)
        content_label.setWordWrap(True)
        content_label.setStyleSheet("font-size: 13px; color: #666; line-height: 1.5;")
        layout.addWidget(content_label, 1)

        if folder_path:
            folder_label = QLabel(
                f'输出位置：{elide_middle(folder_path, 52)} | <a href="open">打开文件夹</a>'
            )
            folder_label.setOpenExternalLinks(False)
            folder_label.linkActivated.connect(
                lambda _: QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))
            )
            folder_label.setWordWrap(True)
            folder_label.setStyleSheet("font-size: 12px; color: #777;")
            layout.addWidget(folder_label)

        button = QPushButton("我知道了")
        button.setFixedHeight(40)
        button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_MAIN_PINK};
                color: #444;
                border: none;
                border-radius: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #FFD1D1;
            }}
            """
        )
        button.clicked.connect(self.accept)
        layout.addWidget(button)

        root.addWidget(card)


class ConfirmDialog(QDialog):
    """用于覆盖同名文件等场景的确认对话框。"""

    def __init__(self, title: str, message: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(380, 220)
        self.result_value = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)

        card = QFrame()
        card.setStyleSheet(
            f"""
            QFrame {{
                background-color: {COLOR_BG};
                border-radius: 24px;
                border: 1px solid #F0E4D7;
            }}
            """
        )

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 45))
        shadow.setOffset(0, 8)
        card.setGraphicsEffect(shadow)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #555;")
        layout.addWidget(title_label)

        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setStyleSheet("font-size: 13px; color: #666; line-height: 1.5;")
        layout.addWidget(message_label, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedHeight(40)
        btn_cancel.setStyleSheet(
            """
            QPushButton {
                background-color: #EAEAEA;
                color: #555;
                border: none;
                border-radius: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #DFDFDF;
            }
            """
        )
        btn_cancel.clicked.connect(self.reject)

        btn_confirm = QPushButton("覆盖")
        btn_confirm.setFixedHeight(40)
        btn_confirm.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_MAIN_PINK};
                color: #444;
                border: none;
                border-radius: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #FFD1D1;
            }}
            """
        )
        btn_confirm.clicked.connect(self.accept)

        button_row.addWidget(btn_cancel)
        button_row.addWidget(btn_confirm)
        layout.addLayout(button_row)
        root.addWidget(card)


class PreviewDropFrame(QFrame):
    """
    预览区控件：
    1. 支持点击打开视频文件。
    2. 支持拖拽本地视频文件进入预览区。
    """

    clicked = pyqtSignal()
    file_dropped = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                path = urls[0].toLocalFile()
                if is_supported_video(path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                self.file_dropped.emit(urls[0].toLocalFile())
                event.acceptProposedAction()
                return
        event.ignore()


class JumpSlider(QSlider):
    """
    支持点击轨道直接跳转的进度条。
    保留原生拖动行为，同时新增“点击某个位置立刻跳过去”的交互。
    """

    jump_clicked = pyqtSignal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle_rect = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider,
                option,
                QStyle.SubControl.SC_SliderHandle,
                self,
            )

            if not handle_rect.contains(event.position().toPoint()):
                value = QStyle.sliderValueFromPosition(
                    self.minimum(),
                    self.maximum(),
                    int(event.position().x()),
                    max(1, self.width()),
                )
                self.setValue(value)
                self.jump_clicked.emit(value)
                event.accept()
                return

        super().mousePressEvent(event)


class VideoThread(QThread):
    """
    子线程视频读取器。

    目标：
    1. 所有 cv2.read() 与 cv2.resize() 都在子线程执行。
    2. 主线程只接收已经处理好的 QImage，然后直接显示。
    3. 播放时尽量使用顺序读取，只在手动 seek 或循环回到片段开头时执行 set(POS_FRAMES)。
    """

    metadata_loaded = pyqtSignal(dict)
    frame_ready = pyqtSignal(int, QImage)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._running = True

        self._capture: cv2.VideoCapture | None = None
        self._video_path: str | None = None
        self._fps = DEFAULT_PREVIEW_FPS
        self._total_frames = 0
        self._duration_sec = 0.0
        self._current_frame_index = -1

        self._pending_load_path: str | None = None
        self._pending_seek_frame: int | None = None
        self._resume_playback_after_seek = False

        self._playback_active = False
        self._playback_start_frame = 0
        self._playback_end_frame = 0
        self._playback_interval = 1.0 / DEFAULT_PREVIEW_FPS
        self._playback_source_fps = DEFAULT_PREVIEW_FPS
        self._playback_started_at = 0.0
        self._last_frame_time = 0.0

    def load_video(self, path: str):
        """请求线程加载一个新视频。"""
        with self._lock:
            self._pending_load_path = path
            self._pending_seek_frame = None
            self._resume_playback_after_seek = False
            self._playback_active = False

    def seek_to_frame(self, frame_index: int):
        """请求线程跳到指定帧，并只显示这一帧。"""
        with self._lock:
            self._pending_seek_frame = max(0, frame_index)
            self._resume_playback_after_seek = False
            self._playback_active = False

    def start_preview(self, start_frame: int, end_frame: int, fps: float):
        """
        请求线程从指定帧开始预览片段。
        这里会先 seek 到片段开头并显示第一帧，然后再顺序播放。
        """
        with self._lock:
            self._playback_start_frame = max(0, start_frame)
            self._playback_end_frame = max(self._playback_start_frame, end_frame)
            self._playback_interval = 1.0 / max(1.0, fps)
            self._playback_source_fps = max(1.0, self._fps)
            self._playback_started_at = 0.0
            self._pending_seek_frame = self._playback_start_frame
            self._resume_playback_after_seek = True
            self._playback_active = False

    def pause_playback(self):
        """暂停播放，但保留当前帧位置。"""
        with self._lock:
            self._playback_active = False
            self._resume_playback_after_seek = False

    def stop_thread(self):
        """请求线程安全退出。"""
        with self._lock:
            self._running = False
            self._playback_active = False
            self._resume_playback_after_seek = False

    def run(self):
        """线程主循环。"""
        while True:
            with self._lock:
                running = self._running
                pending_load_path = self._pending_load_path
                pending_seek_frame = self._pending_seek_frame
                playback_active = self._playback_active
                playback_start_frame = self._playback_start_frame
                playback_end_frame = self._playback_end_frame
                playback_interval = self._playback_interval
                playback_source_fps = self._playback_source_fps
                playback_started_at = self._playback_started_at

            if not running:
                break

            if pending_load_path is not None:
                self._handle_load(pending_load_path)
                continue

            if self._capture is None:
                self.msleep(10)
                continue

            if pending_seek_frame is not None:
                self._handle_seek(pending_seek_frame)
                continue

            if playback_active:
                now = time.monotonic()
                remain = playback_interval - (now - self._last_frame_time)
                if remain > 0:
                    self.msleep(max(1, int(remain * 1000)))
                    continue

                clip_length = max(1, playback_end_frame - playback_start_frame + 1)
                if playback_started_at <= 0:
                    desired_frame = playback_start_frame
                else:
                    elapsed = max(0.0, now - playback_started_at)
                    desired_offset = int(elapsed * playback_source_fps) % clip_length
                    desired_frame = playback_start_frame + desired_offset

                if desired_frame < self._current_frame_index:
                    self._seek_capture(desired_frame)
                    success, frame = self._capture.read()
                    frame_index = desired_frame
                else:
                    advance = desired_frame - self._current_frame_index
                    if advance <= 0:
                        advance = 1

                    if advance <= 5:
                        for _ in range(max(0, advance - 1)):
                            if not self._capture.grab():
                                break
                        success, frame = self._capture.read()
                    else:
                        self._seek_capture(desired_frame)
                        success, frame = self._capture.read()

                    frame_index = desired_frame

                if not success or frame is None:
                    self._seek_capture(desired_frame)
                    success, frame = self._capture.read()
                    frame_index = desired_frame

                if success and frame is not None:
                    image = self._prepare_qimage(frame)
                    self._current_frame_index = frame_index
                    self._last_frame_time = time.monotonic()
                    self.frame_ready.emit(frame_index, image)
                else:
                    self.error_occurred.emit("视频帧读取失败，已停止预览。")
                    self.pause_playback()
                continue

            self.msleep(10)

        self._release_capture()

    def _handle_load(self, path: str):
        """在线程中打开视频并读取元信息。"""
        with self._lock:
            if self._pending_load_path != path:
                return
            self._pending_load_path = None

        self._release_capture()

        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            self.error_occurred.emit("视频文件无法打开，可能是格式异常或编码不受支持。")
            return

        fps = capture.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            fps = DEFAULT_PREVIEW_FPS

        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            total_frames = 1

        self._capture = capture
        self._video_path = path
        self._fps = fps
        self._total_frames = max(1, total_frames)
        self._duration_sec = self._total_frames / self._fps if self._fps > 0 else 0.0
        self._current_frame_index = -1
        self._last_frame_time = 0.0

        metadata = {
            "path": path,
            "fps": self._fps,
            "total_frames": self._total_frames,
            "duration_sec": self._duration_sec,
        }
        self.metadata_loaded.emit(metadata)

        with self._lock:
            self._pending_seek_frame = 0
            self._resume_playback_after_seek = False
            self._playback_active = False

    def _handle_seek(self, frame_index: int):
        """在线程中执行随机定位，只在必要时调用 CAP_PROP_POS_FRAMES。"""
        with self._lock:
            if self._pending_seek_frame != frame_index:
                return
            self._pending_seek_frame = None
            resume_playback_after_seek = self._resume_playback_after_seek
            self._resume_playback_after_seek = False

        if self._capture is None:
            return

        frame_index = max(0, min(frame_index, self._total_frames - 1))
        self._seek_capture(frame_index)
        success, frame = self._capture.read()
        if not success or frame is None:
            self.error_occurred.emit("视频定位失败，请尝试重新选择该片段。")
            with self._lock:
                self._playback_active = False
            return

        image = self._prepare_qimage(frame)
        self._current_frame_index = frame_index
        self._last_frame_time = time.monotonic()
        self.frame_ready.emit(frame_index, image)

        if resume_playback_after_seek:
            with self._lock:
                self._playback_started_at = time.monotonic()
                self._playback_active = True

    def _seek_capture(self, frame_index: int):
        """真正执行 OpenCV 的 seek。"""
        if self._capture is not None:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)

    def _prepare_qimage(self, frame_bgr) -> QImage:
        """
        在子线程中完成帧预处理：
        1. 先按长边 512 缩放，短边等比变化
        2. 再转成 RGB
        3. 最后构建 QImage 发送给主线程
        """
        height, width = frame_bgr.shape[:2]
        long_edge = max(width, height)
        scale = PREVIEW_MAX_EDGE / max(1, long_edge)
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))

        resized = cv2.resize(
            frame_bgr,
            (new_width, new_height),
            interpolation=cv2.INTER_AREA if scale <= 1 else cv2.INTER_LINEAR,
        )
        frame_rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        bytes_per_line = frame_rgb.shape[1] * frame_rgb.shape[2]
        return QImage(
            frame_rgb.data,
            frame_rgb.shape[1],
            frame_rgb.shape[0],
            bytes_per_line,
            QImage.Format.Format_RGB888,
        ).copy()

    def _release_capture(self):
        """释放当前视频句柄。"""
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class ConversionWorker(QObject):
    """
    FFmpeg 转码工作对象。
    转码仍然放在子线程里，避免界面在压制阶段卡住。
    """

    progress = pyqtSignal(str)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        ffmpeg_path: str,
        input_path: str,
        output_path: str,
        start_time: float,
        source_fps: float,
    ):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.input_path = input_path
        self.output_path = output_path
        self.start_time = max(0.0, start_time)
        self.source_fps = source_fps if source_fps and source_fps > 0 else DEFAULT_PREVIEW_FPS

    def run(self):
        """执行多轮码率压制，直到文件大小满足约束。"""
        bitrate = INITIAL_BITRATE_KBPS
        best_temp_path = None
        normalized_temp_path = None
        output_dir = Path(self.output_path).parent
        encode_input_path = self.input_path
        encode_start_time = self.start_time
        encode_duration = TARGET_DURATION_SEC
        encode_fps = self.source_fps

        try:
            if self.source_fps > 30.0:
                self.progress.emit("检测到高帧率视频，先标准化为 29.975 FPS 片段…")
                with tempfile.NamedTemporaryFile(
                    suffix=".mp4",
                    delete=False,
                    dir=output_dir,
                ) as normalized_file:
                    normalized_temp_path = normalized_file.name

                self._prepare_normalized_segment(normalized_temp_path)
                encode_input_path = normalized_temp_path
                encode_start_time = 0.0
                encode_duration = TARGET_DURATION_SEC
                encode_fps = 0.0

            for attempt in range(1, MAX_ENCODE_ATTEMPTS + 1):
                self.progress.emit(f"正在转换，第 {attempt} 次压制，目标码率 {bitrate} kbps…")

                with tempfile.NamedTemporaryFile(
                    suffix=".webm",
                    delete=False,
                    dir=output_dir,
                ) as temp_file:
                    temp_output = temp_file.name

                self._run_ffmpeg(
                    temp_output,
                    bitrate,
                    encode_input_path,
                    encode_start_time,
                    encode_duration,
                    encode_fps,
                )
                current_size = os.path.getsize(temp_output)
                best_temp_path = temp_output

                if current_size < TARGET_MAX_BYTES:
                    if os.path.exists(self.output_path):
                        os.remove(self.output_path)
                    os.replace(temp_output, self.output_path)
                    best_temp_path = None
                    self.finished.emit(f"贴图已生成成功，文件大小约 {current_size / 1024:.1f} KB。")
                    return

                ratio = TARGET_MAX_BYTES / max(current_size, 1)
                adaptive_factor = min(0.80, ratio * 0.95)
                next_bitrate = max(MIN_BITRATE_KBPS, int(bitrate * adaptive_factor))
                if next_bitrate >= bitrate:
                    next_bitrate = max(MIN_BITRATE_KBPS, bitrate - 20)
                bitrate = next_bitrate

                if os.path.exists(temp_output):
                    os.remove(temp_output)
                    best_temp_path = None

            self.failed.emit("压制多次后仍未能稳定小于 256KB，请尝试选择更简单的画面片段。")
        except Exception as exc:
            self.failed.emit(f"生成贴图失败：{exc}")
        finally:
            if best_temp_path and os.path.exists(best_temp_path):
                os.remove(best_temp_path)
            if normalized_temp_path and os.path.exists(normalized_temp_path):
                os.remove(normalized_temp_path)

    def _build_vf_chain(self, source_fps: float, include_scale: bool = True) -> str:
        """
        统一构建滤镜链。

        关键约束：
        1. 对高于 29.975 FPS 的素材，必须先做 fps 重采样，再做缩放。
        2. fps 与 scale 必须放在同一个 -vf 参数里，避免多段处理后时间轴出现偏差。
        """
        filters: list[str] = []

        if source_fps > TARGET_FPS:
            filters.append(f"fps={TARGET_FPS}")

        if include_scale:
            filters.append("scale='if(gte(iw,ih),512,-2)':'if(gte(iw,ih),-2,512)'")

        return ",".join(filters)

    def _build_cfr_args(self, output_fps: float) -> list[str]:
        """
        构建恒定帧率参数。

        这里显式使用 `-fps_mode cfr`，让 FFmpeg 输出严格的恒定帧率；
        同时保留 `-r` 指定目标输出帧率，帮助 Telegram 贴纸结果更稳定。
        """
        return [
            "-r",
            f"{output_fps:.3f}",
            "-fps_mode",
            "cfr",
        ]

    def _prepare_normalized_segment(self, output_path: str):
        """
        对高帧率视频先截出目标片段，并标准化到 29.975 FPS。
        后续 VP9 压制再基于这个临时片段进行，避免高帧率源视频直接转码时出现加速感。
        """
        vf_chain = self._build_vf_chain(self.source_fps, include_scale=False)
        command = [
            self.ffmpeg_path,
            "-y",
            "-ss",
            f"{self.start_time:.3f}",
            "-i",
            self.input_path,
            "-t",
            f"{TARGET_DURATION_SEC:.3f}",
            "-an",
            "-vf",
            vf_chain,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
        command.extend(self._build_cfr_args(TARGET_FPS))
        command.append(output_path)

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=SUBPROCESS_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or "标准化高帧率片段失败。"
            raise RuntimeError(stderr)

    def _run_ffmpeg(
        self,
        output_path: str,
        bitrate_kbps: int,
        input_path: str,
        start_time: float,
        duration: float,
        source_fps: float,
    ):
        """
        调用 FFmpeg 按 Telegram 视频贴纸要求导出 WebM。
        """
        output_fps = min(source_fps, TARGET_FPS) if source_fps > 0 else TARGET_FPS
        vf_arg = self._build_vf_chain(source_fps, include_scale=True)

        command = [
            self.ffmpeg_path,
            "-y",
            "-ss",
            f"{start_time:.3f}",
            "-i",
            input_path,
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            f"{bitrate_kbps}k",
            "-minrate",
            f"{bitrate_kbps}k",
            "-maxrate",
            f"{bitrate_kbps}k",
            "-bufsize",
            f"{bitrate_kbps * 2}k",
            "-pix_fmt",
            "yuv420p",
            "-deadline",
            "good",
            "-cpu-used",
            "4",
            "-row-mt",
            "1",
            "-threads",
            "4",
            "-vf",
            vf_arg,
        ]
        command.extend(self._build_cfr_args(output_fps))
        command.append(output_path)

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=SUBPROCESS_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or "FFmpeg 未返回具体错误信息。"
            raise RuntimeError(stderr)


class MainWindow(QWidget):
    """主窗口：只负责界面交互与状态协同，不再直接做重型视频处理。"""

    def __init__(self):
        super().__init__()

        # FFmpeg 不和打包资源走同一套路径：
        # resources 会被 PyInstaller 收进内部目录，
        # 但 bin/ffmpeg.exe 是我们在打包后手动复制到 EXE 同级目录中的。
        # 所以这里必须以“程序真实运行目录”为基准去找。
        self.ffmpeg_path = os.path.join(runtime_base_path(), "bin", "ffmpeg.exe")
        self.video_path: str | None = None
        self.video_fps = DEFAULT_PREVIEW_FPS
        self.total_frames = 0
        self.duration_sec = 0.0
        self.current_frame_index = 0
        self.selected_start_frame = 0
        self.is_slider_dragging = False
        self.is_playing_preview = False
        self.keep_paused_mode = False
        self.pending_output_path: str | None = None
        self.settings_visible = False
        self.centered_once = False
        self.old_pos: QPoint | None = None
        self.waiting_first_frame = False

        self.idle_preview_timer = QTimer(self)
        self.idle_preview_timer.setSingleShot(True)
        self.idle_preview_timer.timeout.connect(self.start_auto_preview)

        self.convert_thread: QThread | None = None
        self.convert_worker: ConversionWorker | None = None

        self.video_thread = VideoThread()
        self.video_thread.metadata_loaded.connect(self.on_video_metadata_loaded)
        self.video_thread.frame_ready.connect(self.on_video_frame_ready)
        self.video_thread.error_occurred.connect(self.on_video_error)
        self.video_thread.start()

        self.setWindowTitle("TG视频贴纸转换器")
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)

        icon_path = find_first_existing_path(
            [
                resource_path("resources", "app_icon.png"),
                resource_path("resources", "app_icon.png.png"),
            ]
        )
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.init_ui()
        self.update_preview_button_text()
        self.update_controls_enabled(False)
        self.check_ffmpeg_on_startup()
        self.center_on_screen()

    def init_ui(self):
        """构建界面，并尽量保持你原先的视觉风格。"""
        self.container = QFrame(self)
        self.container.setObjectName("MainContainer")
        self.container.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.container.setStyleSheet(
            f"""
            QFrame#MainContainer {{
                background-color: {COLOR_BG};
                border-radius: 30px;
            }}
            """
        )

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(30, 20, 30, 24)
        layout.setSpacing(14)

        # ------------------------------
        # 自定义标题栏
        # ------------------------------
        title_layout = QHBoxLayout()
        title_layout.setSpacing(10)

        title_icon = QLabel()
        title_icon.setFixedSize(22, 22)
        icon_path = find_first_existing_path(
            [
                resource_path("resources", "app_icon.png"),
                resource_path("resources", "app_icon.png.png"),
            ]
        )
        if icon_path:
            title_icon.setPixmap(
                QPixmap(icon_path).scaled(
                    22,
                    22,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        title_layout.addWidget(title_icon)

        title_label = QLabel("TG 视频贴纸转换器")
        title_label.setStyleSheet("font-weight: bold; color: #555; font-size: 14px;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        sys_btn_style = (
            "QPushButton { background: #EEE; border-radius: 12px; color: #666; "
            "font-size: 14px; font-weight: bold; border: none; } "
            "QPushButton:hover { background: #DDD; }"
        )

        btn_min = QPushButton("－")
        btn_min.setFixedSize(32, 32)
        btn_min.setStyleSheet(sys_btn_style)
        btn_min.clicked.connect(self.showMinimized)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(32, 32)
        btn_close.setStyleSheet(
            sys_btn_style + "QPushButton:hover { background: #FFB3B3; color: white; }"
        )
        btn_close.clicked.connect(self.close)

        title_layout.addWidget(btn_min)
        title_layout.addWidget(btn_close)
        layout.addLayout(title_layout)

        # ------------------------------
        # 视频预览区
        # ------------------------------
        self.preview_frame = PreviewDropFrame()
        self.preview_frame.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self.preview_frame.setStyleSheet(
            f"""
            QFrame {{
                background-color: white;
                border: 2px solid {COLOR_MAIN_BLUE};
                border-radius: 25px;
            }}
            """
        )
        self.preview_frame.clicked.connect(self.open_video_dialog)
        self.preview_frame.file_dropped.connect(self.load_video)

        preview_inner_layout = QVBoxLayout(self.preview_frame)
        # 预览区内部留出完全对称的安全边距，避免视频显示时视觉上偏向某一侧。
        preview_inner_layout.setContentsMargins(4, 4, 4, 4)
        preview_inner_layout.setSpacing(0)

        self.label_main_img = QLabel()
        self.label_main_img.setMinimumSize(PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE)
        self.label_main_img.setMaximumSize(PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE)
        self.label_main_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_main_img.setStyleSheet(
            """
            QLabel {
                background: transparent;
                border: none;
                border-radius: 18px;
                color: #999;
                font-size: 14px;
            }
            """
        )
        preview_inner_layout.addStretch(1)
        preview_inner_layout.addWidget(
            self.label_main_img,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        preview_inner_layout.addStretch(1)
        layout.addWidget(self.preview_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        self.set_preview_placeholder()

        self.time_label = QLabel("00:00.00 / 00:00.00")
        self.time_label.setFixedHeight(20)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setStyleSheet("QLabel { color: #8A8A8A; font-size: 12px; background: transparent; border: none; padding: 0px; }")
        layout.addWidget(self.time_label)

        self.slider = JumpSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setFixedHeight(24)
        self.slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                height: 8px;
                background: #DDD;
                border-radius: 4px;
            }}
            QSlider::sub-page:horizontal {{
                background: {COLOR_MAIN_BLUE};
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {COLOR_MAIN_PINK};
                width: 18px;
                height: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }}
            """
        )
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.slider.valueChanged.connect(self.on_slider_value_changed)
        self.slider.jump_clicked.connect(self.on_slider_jump_clicked)
        layout.addWidget(self.slider)

        # ------------------------------
        # 视频控制按钮行
        # ------------------------------
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(8)

        btn_style = (
            f"QPushButton {{ background-color: {COLOR_MAIN_BLUE}; border-radius: 12px; "
            "padding: 8px 10px; font-weight: bold; color: #444; border: none; }}"
            "QPushButton:hover { background-color: #A0D8F1; }"
            "QPushButton:disabled { background-color: #E5EEF2; color: #B5BDC2; }"
        )

        self.btn_back_10 = QPushButton("前十帧")
        self.btn_prev_frame = QPushButton("前一帧")
        self.btn_preview_toggle = QPushButton("预览")
        self.btn_next_frame = QPushButton("后一帧")
        self.btn_forward_10 = QPushButton("后十帧")

        self.control_buttons = [
            self.btn_back_10,
            self.btn_prev_frame,
            self.btn_preview_toggle,
            self.btn_next_frame,
            self.btn_forward_10,
        ]
        for button in self.control_buttons:
            button.setStyleSheet(btn_style)
            ctrl_layout.addWidget(button)

        self.btn_back_10.clicked.connect(lambda: self.step_frame(-10))
        self.btn_prev_frame.clicked.connect(lambda: self.step_frame(-1))
        self.btn_preview_toggle.clicked.connect(self.toggle_preview)
        self.btn_next_frame.clicked.connect(lambda: self.step_frame(1))
        self.btn_forward_10.clicked.connect(lambda: self.step_frame(10))
        layout.addLayout(ctrl_layout)

        # ------------------------------
        # 功能按钮行
        # ------------------------------
        action_layout = QHBoxLayout()

        self.btn_set = QPushButton("⚙ 输出设置")
        self.btn_set.setFixedHeight(46)
        self.btn_set.setStyleSheet(
            btn_style + "QPushButton { background-color: #B0C4DE; border-radius: 15px; }"
        )
        self.btn_set.clicked.connect(self.toggle_settings)

        self.btn_create = QPushButton("✨ 生成 WebM 贴图")
        self.btn_create.setFixedHeight(46)
        self.btn_create.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_MAIN_PINK};
                border-radius: 15px;
                font-size: 15px;
                font-weight: bold;
                color: #444;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #FFD1D1;
            }}
            QPushButton:disabled {{
                background-color: #F1DEDE;
                color: #AAA;
            }}
            """
        )
        self.btn_create.clicked.connect(self.start_convert)

        action_layout.addWidget(self.btn_set, 1)
        action_layout.addWidget(self.btn_create, 2)
        layout.addLayout(action_layout)

        # ------------------------------
        # 底部信息区
        # ------------------------------
        self.info_panel = QFrame()
        self.info_panel.setStyleSheet("QFrame { background: transparent; border: none; }")
        self.info_panel.setFixedHeight(152)
        info_layout = QVBoxLayout(self.info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(8)

        self.file_info_label = QLabel("点击或拖拽视频到上方预览区域")
        self.file_info_label.setFixedHeight(50)
        self.file_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_info_label.setWordWrap(True)
        self.file_info_label.setStyleSheet(status_chip_style(12))
        info_layout.addWidget(self.file_info_label)

        self.status_label = QLabel("准备就绪，请先加载一个视频。")
        self.status_label.setFixedHeight(50)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(status_chip_style(12))
        info_layout.addWidget(self.status_label)

        self.output_link_label = QLabel(" ")
        self.output_link_label.setFixedHeight(36)
        self.output_link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.output_link_label.setWordWrap(True)
        self.output_link_label.setOpenExternalLinks(False)
        self.output_link_label.linkActivated.connect(self.open_output_folder)
        self.output_link_label.setStyleSheet(status_chip_style(11))
        info_layout.addWidget(self.output_link_label)

        layout.addWidget(self.info_panel)

        footer = QLabel(
            '作者: 夜の猫 | <a href="https://imneko.com" '
            'style="color:#89CFF0; text-decoration:none;">imneko.com</a>'
        )
        footer.setOpenExternalLinks(True)
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet("color: #AAA; font-size: 11px;")
        layout.addWidget(footer)

        # ------------------------------
        # 悬浮设置卡片
        # ------------------------------
        self.settings_card = QFrame(self.container)
        self.settings_card.setGeometry(QRect(50, WINDOW_HEIGHT, 500, 240))
        self.settings_card.setStyleSheet(
            "QFrame { background-color: white; border: 1px solid #EEE; border-radius: 20px; }"
        )

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 8)
        self.settings_card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.settings_card)
        card_layout.setContentsMargins(20, 15, 20, 15)

        header_layout = QHBoxLayout()
        header_title = QLabel("⚙ 贴图输出设置")
        header_title.setStyleSheet(
            "font-weight: bold; color: #555; font-size: 16px; border: none;"
        )
        header_layout.addWidget(header_title)
        card_layout.addLayout(header_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #F0F0F0;")
        card_layout.addWidget(line)

        self.radio1 = QRadioButton("保存至视频原目录")
        self.radio1.setChecked(True)
        self.radio1.setStyleSheet(
            "QRadioButton { color: #666; font-size: 14px; border: none; }"
        )

        self.radio2 = QRadioButton("保存至自定义目录")
        self.radio2.setStyleSheet(
            "QRadioButton { color: #666; font-size: 14px; border: none; }"
        )

        card_layout.addWidget(self.radio1)
        card_layout.addWidget(self.radio2)

        path_box = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("点击右侧浏览选择文件夹...")
        self.path_edit.setStyleSheet(
            "QLineEdit { background: #F9F9F9; border: 1px solid #DDD; "
            "border-radius: 8px; padding: 6px; }"
        )
        self.path_edit.setEnabled(False)

        self.btn_browse = QPushButton("浏览")
        self.btn_browse.setFixedWidth(60)
        self.btn_browse.setStyleSheet(
            f"background: {COLOR_MAIN_BLUE}; border-radius: 8px; padding: 5px; border: none;"
        )
        self.btn_browse.setEnabled(False)
        self.btn_browse.clicked.connect(self.choose_output_directory)

        self.radio1.toggled.connect(self.on_output_mode_changed)
        self.radio2.toggled.connect(self.on_output_mode_changed)
        self.path_edit.textChanged.connect(self.update_settings_confirm_enabled)

        path_box.addWidget(self.path_edit)
        path_box.addWidget(self.btn_browse)
        card_layout.addLayout(path_box)
        card_layout.addSpacing(10)

        self.btn_done = QPushButton("确定")
        self.btn_done.setFixedHeight(35)
        self.btn_done.clicked.connect(self.confirm_settings)
        card_layout.addWidget(self.btn_done)
        self.update_settings_confirm_enabled()

    # ------------------------------
    # 通用 UI 帮助方法
    # ------------------------------
    def show_message(self, title: str, message: str, folder_path: str | None = None):
        """弹出统一风格的提示框。"""
        dialog = RoundedMessageDialog(title, message, self, folder_path=folder_path)
        dialog.exec()

    def set_status(self, text: str):
        """更新状态文本。"""
        self.status_label.setText(text)

    def center_on_screen(self):
        """让窗口启动时默认出现在主显示器可用区域中央。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.x() + (available.width() - self.width()) // 2
        y = available.y() + (available.height() - self.height()) // 2
        self.move(max(available.x(), x), max(available.y(), y))

    def showEvent(self, event):
        """首次显示时再做一次居中，避免无边框窗口默认跑到左上角。"""
        super().showEvent(event)
        if not self.centered_once:
            self.center_on_screen()
            self.centered_once = True

    def set_file_info(self, line1: str, line2: str | None = None):
        """设置文件信息区，默认分两行显示。"""
        if line2:
            self.file_info_label.setText(f"{line1}\n{line2}")
        else:
            self.file_info_label.setText(line1)

    def update_done_button_style(self):
        """根据可用状态刷新设置卡片“确定”按钮的颜色与文字颜色。"""
        if self.btn_done.isEnabled():
            self.btn_done.setStyleSheet(
                f"""
                QPushButton {{
                    background: {COLOR_MAIN_PINK};
                    border-radius: 12px;
                    font-weight: bold;
                    color: #444;
                    border: none;
                }}
                QPushButton:hover {{
                    background: #FFD1D1;
                }}
                """
            )
        else:
            self.btn_done.setStyleSheet(
                """
                QPushButton {
                    background: #E8E8E8;
                    border-radius: 12px;
                    font-weight: bold;
                    color: #A5A5A5;
                    border: none;
                }
                """
            )

    def set_output_link(self, file_path: str | None):
        """在主界面下方显示输出文件夹位置，并提供点击打开入口。"""
        if not file_path:
            self.output_link_label.setText(" ")
            self.output_link_label.setProperty("folder_path", "")
            return

        folder_path = str(Path(file_path).parent)
        display_path = elide_middle(folder_path, 40)
        self.output_link_label.setText(
            f'输出位置：{display_path} | <a href="open">打开文件夹</a>'
        )
        self.output_link_label.setProperty("folder_path", folder_path)

    def open_output_folder(self, _link: str | None = None):
        """打开最近一次输出所在的文件夹。"""
        folder_path = self.output_link_label.property("folder_path") or ""
        if folder_path and os.path.isdir(folder_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))

    def show_success_message(self, title: str, message: str, file_path: str | None = None):
        """带输出目录信息的成功提示。"""
        folder_path = str(Path(file_path).parent) if file_path else None
        self.show_message(title, message, folder_path=folder_path)

    def confirm_overwrite(self, output_path: str) -> bool:
        """当同名文件已存在时，询问用户是否覆盖。"""
        dialog = ConfirmDialog(
            "发现同名文件",
            f"输出目录中已经存在同名文件：\n{elide_middle(output_path, 48)}\n\n是否覆盖原文件？",
            self,
        )
        return dialog.exec() == QDialog.DialogCode.Accepted

    def set_preview_placeholder(self):
        """显示程序默认占位图。"""
        main_image = resource_path("resources", "app_main.png")
        if os.path.exists(main_image):
            pixmap = QPixmap(main_image).scaled(
                480,
                480,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.label_main_img.setPixmap(pixmap)
            self.label_main_img.setText("")
        else:
            self.label_main_img.setPixmap(QPixmap())
            self.label_main_img.setText("请在 resources 中放置 app_main.png")

    def update_controls_enabled(self, enabled: bool):
        """统一启用或禁用与视频相关的控件。"""
        self.slider.setEnabled(enabled)
        self.btn_create.setEnabled(enabled)
        for button in self.control_buttons:
            button.setEnabled(enabled)

    def update_preview_button_text(self):
        """
        同步播放按钮的文案与颜色：
        - 播放中显示“暂停”，背景为粉红色
        - 暂停时显示“播放”，背景为草绿色
        """
        if self.is_playing_preview:
            self.btn_preview_toggle.setText("暂停")
            self.btn_preview_toggle.setStyleSheet(
                """
                QPushButton {
                    background-color: #F4C2C2;
                    border-radius: 12px;
                    padding: 8px 10px;
                    font-weight: bold;
                    color: #444;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #FFD1D1;
                }
                QPushButton:disabled {
                    background-color: #F1DEDE;
                    color: #AAA;
                }
                """
            )
        else:
            self.btn_preview_toggle.setText("播放")
            self.btn_preview_toggle.setStyleSheet(
                """
                QPushButton {
                    background-color: #A8D8A0;
                    border-radius: 12px;
                    padding: 8px 10px;
                    font-weight: bold;
                    color: #444;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #BCE7B5;
                }
                QPushButton:disabled {
                    background-color: #D7E7D3;
                    color: #AAA;
                }
                """
            )

    def update_time_label(self, frame_index: int | None = None):
        """根据当前帧刷新“当前时间 / 总时间”文本。"""
        if frame_index is None:
            frame_index = self.slider.value()

        current_sec = frame_index / self.video_fps if self.video_fps > 0 else 0.0
        self.time_label.setText(f"{format_seconds(current_sec)} / {format_seconds(self.duration_sec)}")

    def on_output_mode_changed(self):
        """切换输出模式时，启用或禁用自定义目录输入框。"""
        use_custom = self.radio2.isChecked()
        self.path_edit.setEnabled(use_custom)
        self.btn_browse.setEnabled(use_custom)
        self.update_settings_confirm_enabled()

    def update_settings_confirm_enabled(self):
        """只有自定义目录有效时，才允许点击设置卡片的确定按钮。"""
        if self.radio1.isChecked():
            self.btn_done.setEnabled(True)
            self.update_done_button_style()
            return

        custom_dir = self.path_edit.text().strip()
        self.btn_done.setEnabled(bool(custom_dir) and os.path.isdir(custom_dir))
        self.update_done_button_style()

    def choose_output_directory(self):
        """选择自定义导出目录。"""
        directory = QFileDialog.getExistingDirectory(self, "选择贴图输出目录")
        if directory:
            self.path_edit.setText(directory)
        self.update_settings_confirm_enabled()

    def confirm_settings(self):
        """确认输出设置，必要时校验自定义目录后再关闭卡片。"""
        if self.radio2.isChecked():
            custom_dir = self.path_edit.text().strip()
            if not custom_dir or not os.path.isdir(custom_dir):
                self.show_message("目录无效", "请先选择一个有效的自定义输出目录。")
                return
        self.toggle_settings()

    def check_ffmpeg_on_startup(self):
        """启动时检测 FFmpeg 是否存在。"""
        if not os.path.exists(self.ffmpeg_path):
            self.show_message(
                "缺少 FFmpeg",
                "程序启动时没有找到 ./bin/ffmpeg.exe。\n\n请确认 bin 文件夹完整存在，再重新启动程序。"
            )
            self.btn_create.setEnabled(False)
            self.set_status("未找到 FFmpeg，暂时无法生成 WebM 贴图。")

    # ------------------------------
    # 视频加载与线程回调
    # ------------------------------
    def open_video_dialog(self):
        """点击预览区后打开选择文件对话框。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv)",
        )
        if file_path:
            self.load_video(file_path)

    def load_video(self, file_path: str):
        """向视频线程发起加载请求。"""
        if not os.path.exists(file_path):
            self.show_message("文件不存在", "所选视频文件不存在，请重新选择。")
            return

        if not is_supported_video(file_path):
            self.show_message("暂不支持该格式", "请选择常见视频格式，例如 MP4、MOV、MKV、AVI、WebM。")
            return

        self.pause_preview(schedule_resume=False)
        self.video_path = None
        self.waiting_first_frame = True
        self.keep_paused_mode = True
        self.update_controls_enabled(False)
        self.slider.blockSignals(True)
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.update_time_label(0)
        self.set_file_info("尚未完成加载", f"文件：{elide_middle(Path(file_path).name, 34)}")
        self.set_status(f"正在加载：{Path(file_path).name}")
        self.video_thread.load_video(file_path)

    def on_video_metadata_loaded(self, metadata: dict):
        """收到线程返回的元信息后，刷新主线程状态。"""
        self.video_path = metadata["path"]
        self.video_fps = metadata["fps"]
        self.total_frames = metadata["total_frames"]
        self.duration_sec = metadata["duration_sec"]
        self.current_frame_index = 0
        self.selected_start_frame = 0
        self.keep_paused_mode = True

        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, self.total_frames - 1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)

        self.update_time_label(0)
        self.update_controls_enabled(True)
        self.set_file_info(
            f"已加载：{elide_middle(Path(self.video_path).name, 38)}",
            f"帧率 {self.video_fps:.3f} FPS | 时长约 {self.duration_sec:.2f} 秒",
        )
        self.set_status("视频已就绪，请点击“播放”开始预览，或直接定位起始画面。")

    def on_video_frame_ready(self, frame_index: int, image: QImage):
        """
        主线程接收线程处理后的帧图像。
        这里只做最轻量的 UI 更新，不再重复缩放或做圆角裁切。
        """
        self.current_frame_index = frame_index
        self.label_main_img.setPixmap(QPixmap.fromImage(image))
        self.label_main_img.setText("")

        self.slider.blockSignals(True)
        self.slider.setValue(frame_index)
        self.slider.blockSignals(False)
        self.update_time_label(frame_index)

        if self.waiting_first_frame:
            self.waiting_first_frame = False

    def on_video_error(self, message: str):
        """收到子线程错误信息时，在主线程里友好提示。"""
        self.pause_preview(schedule_resume=False)
        self.set_status(message)
        self.show_message("视频处理提示", message)

    # ------------------------------
    # 预览控制逻辑
    # ------------------------------
    def schedule_auto_preview(self):
        """用户停止操作 0.5 秒后，再启动 2.9 秒循环预览。"""
        if not self.video_path or self.keep_paused_mode:
            return
        self.idle_preview_timer.stop()
        self.idle_preview_timer.start(AUTO_PREVIEW_DELAY_MS)

    def start_auto_preview(self):
        """
        从当前进度条位置开始，自动播放后续 2.9 秒内容。
        所有真正的读帧与播放都在视频线程里执行。
        """
        if not self.video_path or self.total_frames <= 0:
            return

        start_frame = self.selected_start_frame
        preview_fps = min(self.video_fps, TARGET_FPS)
        preview_frame_count = max(1, int(round(self.video_fps * AUTO_PREVIEW_DURATION_SEC)))
        end_frame = min(self.total_frames - 1, start_frame + preview_frame_count - 1)

        self.idle_preview_timer.stop()
        self.keep_paused_mode = False
        self.is_playing_preview = True
        self.update_preview_button_text()
        self.video_thread.start_preview(start_frame, end_frame, preview_fps)
        self.set_status(
            f"正在循环预览：从第 {start_frame + 1} 帧开始，持续约 2.9 秒。"
        )

    def pause_preview(self, schedule_resume: bool = False):
        """
        暂停当前预览。
        schedule_resume 用于少数需要在暂停后重新排队自动预览的场景。
        """
        self.idle_preview_timer.stop()
        self.video_thread.pause_playback()
        self.is_playing_preview = False
        self.update_preview_button_text()
        if self.video_path:
            self.set_status("预览已暂停。")
        if schedule_resume:
            self.schedule_auto_preview()

    def toggle_preview(self):
        """合并后的“预览 / 暂停”按钮逻辑。"""
        if not self.video_path:
            return
        if self.is_playing_preview:
            self.keep_paused_mode = True
            self.pause_preview(schedule_resume=False)
        else:
            self.selected_start_frame = self.slider.value()
            self.keep_paused_mode = False
            self.start_auto_preview()

    def on_slider_pressed(self):
        """开始拖动进度条时先暂停播放。"""
        self.is_slider_dragging = True
        self.pause_preview(schedule_resume=False)

    def on_slider_released(self):
        """拖动结束后，根据当前位置重新排队自动预览。"""
        if not self.video_path:
            return
        self.is_slider_dragging = False
        target = self.slider.value()
        self.selected_start_frame = target
        self.video_thread.seek_to_frame(target)
        if not self.keep_paused_mode:
            self.schedule_auto_preview()

    def on_slider_jump_clicked(self, value: int):
        """点击进度条某个位置时立刻跳转。"""
        if not self.video_path:
            return
        self.pause_preview(schedule_resume=False)
        self.selected_start_frame = value
        self.video_thread.seek_to_frame(value)
        self.update_time_label(value)
        self.set_status(f"已跳转到第 {value + 1} 帧。")
        if not self.keep_paused_mode:
            self.schedule_auto_preview()

    def on_slider_value_changed(self, value: int):
        """
        拖动进度条过程中，画面跟着走，但不自动开播。
        这里仅在手动拖动阶段触发定位请求，避免和线程回推 slider 值互相打架。
        """
        self.update_time_label(value)
        if self.video_path and self.is_slider_dragging:
            self.selected_start_frame = value
            self.video_thread.seek_to_frame(value)
            current_sec = value / self.video_fps if self.video_fps > 0 else 0.0
            self.set_status(f"当前定位：第 {value + 1} 帧 / {current_sec:.2f} 秒")

    def step_frame(self, delta: int):
        """
        帧级跳转逻辑。
        注意这里按你的要求，以“进度条当前位置”为起始依据，而不是按线程当前播放状态。
        """
        if not self.video_path:
            return

        self.pause_preview(schedule_resume=False)
        base_frame = self.slider.value()
        target = max(0, min(self.total_frames - 1, base_frame + delta))
        self.selected_start_frame = target

        self.slider.blockSignals(True)
        self.slider.setValue(target)
        self.slider.blockSignals(False)
        self.update_time_label(target)

        self.video_thread.seek_to_frame(target)
        self.set_status(f"已定位到第 {target + 1} 帧。")
        if not self.keep_paused_mode:
            self.schedule_auto_preview()

    # ------------------------------
    # WebM 转换逻辑
    # ------------------------------
    def get_output_directory(self) -> str | None:
        """解析导出目录。"""
        if not self.video_path:
            return None

        if self.radio1.isChecked():
            return str(Path(self.video_path).parent)

        custom_dir = self.path_edit.text().strip()
        if not custom_dir:
            self.show_message("缺少输出目录", "你已选择“保存至自定义目录”，请先选择一个文件夹。")
            return None

        if not os.path.isdir(custom_dir):
            self.show_message("目录无效", "自定义输出目录不存在，请重新选择。")
            return None

        return custom_dir

    def build_output_path(self, output_dir: str) -> str:
        """拼出最终导出的 WebM 文件名。"""
        source_name = Path(self.video_path).stem if self.video_path else "sticker"
        return str(Path(output_dir) / f"{source_name}_tg_sticker.webm")

    def start_convert(self):
        """
        生成 WebM 贴图。
        片段起点使用当前进度条位置，这样更符合用户操作直觉。
        """
        if not self.video_path:
            self.show_message("还没有视频", "请先加载一个视频，再生成 WebM 贴图。")
            return

        if not os.path.exists(self.ffmpeg_path):
            self.show_message("缺少 FFmpeg", "未找到 ./bin/ffmpeg.exe，无法进行视频转换。")
            return

        output_dir = self.get_output_directory()
        if not output_dir:
            return

        output_path = self.build_output_path(output_dir)
        if os.path.exists(output_path) and not self.confirm_overwrite(output_path):
            self.set_status("已取消本次转换。")
            return
        start_time = (
            self.selected_start_frame / self.video_fps if self.video_fps > 0 else 0.0
        )

        self.pending_output_path = output_path
        self.btn_create.setEnabled(False)
        self.set_status("正在准备调用 FFmpeg 进行转换…")

        self.convert_thread = QThread(self)
        self.convert_worker = ConversionWorker(
            self.ffmpeg_path,
            self.video_path,
            output_path,
            start_time,
            self.video_fps,
        )
        self.convert_worker.moveToThread(self.convert_thread)

        self.convert_thread.started.connect(self.convert_worker.run)
        self.convert_worker.progress.connect(self.on_convert_progress)
        self.convert_worker.finished.connect(self.on_convert_success)
        self.convert_worker.failed.connect(self.on_convert_failed)
        self.convert_worker.finished.connect(self.cleanup_convert_thread)
        self.convert_worker.failed.connect(self.cleanup_convert_thread)

        self.convert_thread.start()

    def on_convert_progress(self, message: str):
        """转码过程中的状态回调。"""
        self.set_status(message)

    def on_convert_success(self, message: str):
        """转码成功回调。"""
        self.btn_create.setEnabled(True)
        self.set_status(message)
        self.set_output_link(self.pending_output_path)
        self.show_success_message("转换完成", message, self.pending_output_path)
        self.pending_output_path = None

    def on_convert_failed(self, message: str):
        """转码失败回调。"""
        self.btn_create.setEnabled(True)
        self.set_status(message)
        self.pending_output_path = None
        self.show_message("转换失败", message)

    def cleanup_convert_thread(self):
        """收尾 FFmpeg 工作线程。"""
        if self.convert_thread is not None:
            self.convert_thread.quit()
            self.convert_thread.wait()

        if self.convert_worker is not None:
            self.convert_worker.deleteLater()
            self.convert_worker = None

        if self.convert_thread is not None:
            self.convert_thread.deleteLater()
            self.convert_thread = None

    # ------------------------------
    # 设置卡片动画
    # ------------------------------
    def toggle_settings(self):
        """控制设置卡片从底部滑入或滑出。"""
        self.anim = QPropertyAnimation(self.settings_card, b"geometry")
        self.anim.setDuration(400)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        if not self.settings_visible:
            self.anim.setEndValue(QRect(50, 585, 500, 240))
            self.settings_visible = True
        else:
            self.anim.setEndValue(QRect(50, WINDOW_HEIGHT, 500, 240))
            self.settings_visible = False

        self.anim.start()

    # ------------------------------
    # 窗口拖动逻辑
    # ------------------------------
    def mousePressEvent(self, event):
        """
        点击空白区域时允许拖动主窗口。
        这样不会抢走按钮、滑块和输入框的交互事件。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            target_widget = self.childAt(event.position().toPoint())
            if target_widget in {self.container, self.time_label, self.status_label, self.file_info_label}:
                self.old_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.old_pos is not None:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.old_pos = None
        super().mouseReleaseEvent(event)

    # ------------------------------
    # 生命周期管理
    # ------------------------------
    def closeEvent(self, event):
        """关闭窗口时停止所有后台线程。"""
        self.pause_preview(schedule_resume=False)

        if self.convert_thread is not None:
            self.convert_thread.quit()
            self.convert_thread.wait()

        self.video_thread.stop_thread()
        self.video_thread.wait()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
