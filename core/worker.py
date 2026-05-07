from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout

from PySide6.QtCore import QObject, QThread, Signal


class TransportWorker(QThread):
    finished_msg = Signal(str)
    error = Signal(str)
    log = Signal(str)

    def __init__(self, begin: str, end: str, item: str, times: int, resolution: str, liquid_mode: bool = False):
        super().__init__()
        self.begin = begin
        self.end = end
        self.item = item
        self.times = times
        self.resolution = resolution
        self.liquid_mode = liquid_mode

    def run(self):
        buffer = io.StringIO()
        try:
            import auto_click

            auto_click.reset_stop()
            with redirect_stdout(buffer):
                auto_click.run_transport(self.begin, self.end, self.item, self.times, self.resolution, liquid_mode=self.liquid_mode)
            output = buffer.getvalue().strip()
            if output:
                self.log.emit(output)
            self.finished_msg.emit("搬运任务已完成")
        except KeyboardInterrupt:
            output = buffer.getvalue().strip()
            if output:
                self.log.emit(output)
            self.finished_msg.emit("搬运任务已停止")
        except Exception:
            output = buffer.getvalue().strip()
            if output:
                self.log.emit(output)
            self.error.emit(traceback.format_exc())


class WorkerHandle(QObject):
    log = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.worker: TransportWorker | None = None

    def start(self, begin: str, end: str, item: str, times: int, resolution: str, liquid_mode: bool = False):
        try:
            if self.worker and self.worker.isRunning():
                return False
        except RuntimeError:
            pass

        self.worker = TransportWorker(begin, end, item, times, resolution, liquid_mode)
        self.worker.log.connect(self.log.emit)
        self.worker.finished_msg.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        return True

    def stop(self):
        try:
            import auto_click

            auto_click.request_stop()
        except Exception:
            pass

    def is_running(self) -> bool:
        try:
            return bool(self.worker and self.worker.isRunning())
        except RuntimeError:
            return False

    def _on_finished(self, message: str):
        self.finished.emit(message)

    def _on_error(self, message: str):
        self.error.emit(message)
