# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Qt launcher for running GEM-X demo pipelines from a GUI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    from PySide6.QtCore import QProcess, QSettings, QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    print("[ERROR] PySide6 is required to run the Qt launcher.", flush=True)
    print(
        "Install with: .\\.venv\\Scripts\\python.exe -m pip install \"PySide6>=6.7,<7\"",
        flush=True,
    )
    raise SystemExit(1)


MODE_FULL = "Full 3D (PyTorch)"
MODE_ONNX = "Accelerated (ONNX/TensorRT)"
MODE_2D = "2D Keypoints Only"

RIG_NONE = "None"
RIG_G1 = "Unitree G1 (.csv + .bvh)"


class GemQtLauncher(QWidget):
    """Desktop launcher for GEM-X demo scripts."""

    def __init__(self) -> None:
        super().__init__()
        self.project_root = Path(__file__).resolve().parents[2]
        self.process: QProcess | None = None
        self.last_output_path = self.project_root / "outputs"
        self.settings = QSettings("GEM-X", "QtLauncher")

        self._build_ui()
        self._load_settings()
        self._refresh_mode_ui()
        self._update_command_preview()

    def _build_ui(self) -> None:
        self.setWindowTitle("GEM-X Launcher")
        self.resize(1080, 760)

        root_layout = QVBoxLayout(self)

        form_layout = QGridLayout()
        form_layout.setColumnStretch(1, 1)
        row = 0

        form_layout.addWidget(QLabel("Pipeline"), row, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MODE_FULL, MODE_ONNX, MODE_2D])
        form_layout.addWidget(self.mode_combo, row, 1, 1, 2)
        row += 1

        form_layout.addWidget(QLabel("Video"), row, 0)
        self.video_edit = QLineEdit()
        self.video_btn = QPushButton("Browse...")
        form_layout.addWidget(self.video_edit, row, 1)
        form_layout.addWidget(self.video_btn, row, 2)
        row += 1

        self.output_label = QLabel("Output Root")
        form_layout.addWidget(self.output_label, row, 0)
        self.output_edit = QLineEdit()
        self.output_btn = QPushButton("Browse...")
        form_layout.addWidget(self.output_edit, row, 1)
        form_layout.addWidget(self.output_btn, row, 2)
        row += 1

        self.ckpt_label = QLabel("Checkpoint (Optional)")
        form_layout.addWidget(self.ckpt_label, row, 0)
        self.ckpt_edit = QLineEdit()
        self.ckpt_btn = QPushButton("Browse...")
        form_layout.addWidget(self.ckpt_edit, row, 1)
        form_layout.addWidget(self.ckpt_btn, row, 2)
        row += 1

        form_layout.addWidget(QLabel("Rig Export"), row, 0)
        self.rig_combo = QComboBox()
        self.rig_combo.addItems([RIG_NONE, RIG_G1])
        form_layout.addWidget(self.rig_combo, row, 1, 1, 2)

        root_layout.addLayout(form_layout)

        options_group = QGroupBox("Options")
        options_layout = QGridLayout(options_group)

        self.static_cam_cb = QCheckBox("Static Camera")
        self.verbose_cb = QCheckBox("Verbose Overlays")
        self.render_mhr_cb = QCheckBox("Render MHR")
        self.force_pytorch_cb = QCheckBox("Force PyTorch (ONNX mode)")
        self.no_imgfeat_cb = QCheckBox("No Image Features (ONNX mode)")
        self.ddim_cb = QCheckBox("DDIM Sampling (ONNX mode)")
        self.save_raw_cb = QCheckBox("Keep Raw .pt Files (2D mode)")

        options_layout.addWidget(self.static_cam_cb, 0, 0)
        options_layout.addWidget(self.verbose_cb, 0, 1)
        options_layout.addWidget(self.render_mhr_cb, 0, 2)
        options_layout.addWidget(self.force_pytorch_cb, 1, 0)
        options_layout.addWidget(self.no_imgfeat_cb, 1, 1)
        options_layout.addWidget(self.ddim_cb, 1, 2)
        options_layout.addWidget(self.save_raw_cb, 2, 0)

        options_layout.addWidget(QLabel("2D Detector"), 2, 1)
        self.detector_combo = QComboBox()
        self.detector_combo.addItems(["vitdet", "sam3"])
        options_layout.addWidget(self.detector_combo, 2, 2)

        options_layout.addWidget(QLabel("2D Confidence"), 3, 1)
        self.conf_spin = QDoubleSpinBox()
        self.conf_spin.setRange(0.0, 1.0)
        self.conf_spin.setSingleStep(0.05)
        self.conf_spin.setDecimals(2)
        self.conf_spin.setValue(0.50)
        options_layout.addWidget(self.conf_spin, 3, 2)

        root_layout.addWidget(options_group)

        root_layout.addWidget(QLabel("Command Preview"))
        self.command_preview = QPlainTextEdit()
        self.command_preview.setReadOnly(True)
        self.command_preview.setFixedHeight(64)
        root_layout.addWidget(self.command_preview)

        buttons_layout = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.stop_btn = QPushButton("Stop")
        self.open_output_btn = QPushButton("Open Output Folder")
        self.clear_log_btn = QPushButton("Clear Log")
        buttons_layout.addWidget(self.run_btn)
        buttons_layout.addWidget(self.stop_btn)
        buttons_layout.addWidget(self.open_output_btn)
        buttons_layout.addWidget(self.clear_log_btn)
        buttons_layout.addStretch(1)
        root_layout.addLayout(buttons_layout)

        self.status_label = QLabel("Status: Idle")
        root_layout.addWidget(self.status_label)

        root_layout.addWidget(QLabel("Process Log"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        root_layout.addWidget(self.log_view, 1)

        self.stop_btn.setEnabled(False)

        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.video_btn.clicked.connect(self._browse_video)
        self.output_btn.clicked.connect(self._browse_output_dir)
        self.ckpt_btn.clicked.connect(self._browse_checkpoint)
        self.run_btn.clicked.connect(self._start_process)
        self.stop_btn.clicked.connect(self._stop_process)
        self.open_output_btn.clicked.connect(self._open_output_folder)
        self.clear_log_btn.clicked.connect(self.log_view.clear)

        self.video_edit.textChanged.connect(self._update_command_preview)
        self.output_edit.textChanged.connect(self._update_command_preview)
        self.ckpt_edit.textChanged.connect(self._update_command_preview)
        self.rig_combo.currentTextChanged.connect(self._update_command_preview)
        self.detector_combo.currentTextChanged.connect(self._update_command_preview)
        self.conf_spin.valueChanged.connect(self._update_command_preview)

        for cb in (
            self.static_cam_cb,
            self.verbose_cb,
            self.render_mhr_cb,
            self.force_pytorch_cb,
            self.no_imgfeat_cb,
            self.ddim_cb,
            self.save_raw_cb,
        ):
            cb.toggled.connect(self._update_command_preview)

    def _default_output_for_mode(self, mode: str) -> Path:
        if mode == MODE_FULL:
            return self.project_root / "outputs" / "demo_soma"
        if mode == MODE_ONNX:
            return self.project_root / "outputs" / "demo_soma_onnx"
        return self.project_root / "outputs" / "demo_2d_kp"

    def _default_ckpt(self) -> Path:
        return self.project_root / "inputs" / "pretrained" / "gem_soma.ckpt"

    def _on_mode_changed(self) -> None:
        self._refresh_mode_ui()
        if not self.output_edit.text().strip():
            self.output_edit.setText(str(self._default_output_for_mode(self.mode_combo.currentText())))
        self._update_command_preview()

    def _refresh_mode_ui(self) -> None:
        mode = self.mode_combo.currentText()
        is_full = mode == MODE_FULL
        is_onnx = mode == MODE_ONNX
        is_2d = mode == MODE_2D

        self.output_label.setText("Output Dir" if is_2d else "Output Root")

        self.ckpt_label.setEnabled(not is_2d)
        self.ckpt_edit.setEnabled(not is_2d)
        self.ckpt_btn.setEnabled(not is_2d)

        self.rig_combo.setEnabled(not is_2d)

        self.render_mhr_cb.setEnabled(is_full)
        self.force_pytorch_cb.setEnabled(is_onnx)
        self.no_imgfeat_cb.setEnabled(is_onnx)
        self.ddim_cb.setEnabled(is_onnx)

        self.save_raw_cb.setEnabled(is_2d)
        self.detector_combo.setEnabled(is_2d)
        self.conf_spin.setEnabled(is_2d)

    def _browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Input Video",
            str(self.project_root),
            "Video Files (*.mp4 *.mov *.avi *.mkv *.webm);;All Files (*)",
        )
        if path:
            self.video_edit.setText(path)

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            self.output_edit.text().strip() or str(self.project_root / "outputs"),
        )
        if path:
            self.output_edit.setText(path)

    def _browse_checkpoint(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Checkpoint",
            str(self.project_root / "inputs" / "pretrained"),
            "Checkpoint Files (*.ckpt *.pt *.pth);;All Files (*)",
        )
        if path:
            self.ckpt_edit.setText(path)

    def _build_command(self, strict: bool) -> tuple[str, list[str], Path]:
        mode = self.mode_combo.currentText()
        video_text = self.video_edit.text().strip()
        output_text = self.output_edit.text().strip()
        ckpt_text = self.ckpt_edit.text().strip()
        rig_mode = self.rig_combo.currentText()

        if strict and not video_text:
            raise ValueError("Video path is required.")
        if strict and not output_text:
            raise ValueError("Output path is required.")

        video_path = Path(video_text) if video_text else Path(".")
        output_path = Path(output_text) if output_text else self._default_output_for_mode(mode)
        ckpt_path = Path(ckpt_text) if ckpt_text else None

        if strict and not video_path.exists():
            raise ValueError(f"Video not found: {video_path}")
        if strict and ckpt_path is not None and not ckpt_path.exists():
            raise ValueError(f"Checkpoint not found: {ckpt_path}")

        py_exe = Path(sys.executable)
        if not py_exe.exists():
            py_exe = self.project_root / ".venv" / "Scripts" / "python.exe"
        if strict and not py_exe.exists():
            raise ValueError(f"Python executable not found: {py_exe}")

        if mode == MODE_FULL:
            script = self.project_root / "scripts" / "demo" / "demo_soma.py"
            args = [
                str(script),
                "--video",
                str(video_path),
                "--output_root",
                str(output_path),
            ]
            if ckpt_path is not None:
                args += ["--ckpt", str(ckpt_path)]
            if self.static_cam_cb.isChecked():
                args.append("--static_cam")
            if self.verbose_cb.isChecked():
                args.append("--verbose")
            if self.render_mhr_cb.isChecked():
                args.append("--render_mhr")
            if rig_mode == RIG_G1:
                args.append("--retarget")
            expected_output = output_path / video_path.stem

        elif mode == MODE_ONNX:
            script = self.project_root / "scripts" / "demo" / "demo_soma_onnx.py"
            args = [
                str(script),
                "--video",
                str(video_path),
                "--output_root",
                str(output_path),
            ]
            if ckpt_path is not None:
                args += ["--ckpt", str(ckpt_path)]
            if self.static_cam_cb.isChecked():
                args.append("--static_cam")
            if self.verbose_cb.isChecked():
                args.append("--verbose")
            if self.force_pytorch_cb.isChecked():
                args.append("--force_pytorch")
            if self.no_imgfeat_cb.isChecked():
                args.append("--no-imgfeat")
            if self.ddim_cb.isChecked():
                args.append("--ddim")
            if rig_mode == RIG_G1:
                args.append("--retarget")
            expected_output = output_path / video_path.stem

        else:
            script = self.project_root / "scripts" / "demo" / "demo_2d_keypoints.py"
            args = [
                str(script),
                "--video",
                str(video_path),
                "--output_dir",
                str(output_path),
                "--detector_name",
                self.detector_combo.currentText(),
                "--conf_thr",
                f"{self.conf_spin.value():.2f}",
            ]
            if self.save_raw_cb.isChecked():
                args.append("--save_raw")
            expected_output = output_path

        if strict and not script.exists():
            raise ValueError(f"Script not found: {script}")

        return str(py_exe), args, expected_output

    def _retarget_requested(self) -> bool:
        return self.mode_combo.currentText() != MODE_2D and self.rig_combo.currentText() == RIG_G1

    def _ensure_retarget_ready(self, python_exe: str) -> bool:
        if not self._retarget_requested():
            return True

        check_cmd = [python_exe, "-c", "import soma_retargeter; print(soma_retargeter.__file__)"]
        probe = subprocess.run(
            check_cmd,
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            resolved = probe.stdout.strip() or "soma_retargeter import OK"
            self._append_log(f"[Launcher] Retarget dependency check passed: {resolved}")
            return True

        submodule_dir = self.project_root / "third_party" / "soma-retargeter"
        has_submodule_files = submodule_dir.exists() and any(submodule_dir.iterdir())

        lines = [
            "Rig export requires `soma_retargeter`, but it is not importable.",
            "",
            "Fix from repo root:",
            "1) git submodule update --init --recursive third_party/soma-retargeter",
            "2) .\\.venv\\Scripts\\python.exe -m pip install -e third_party/soma-retargeter",
            "3) .\\.venv\\Scripts\\python.exe -c \"import soma_retargeter; print(soma_retargeter.__file__)\"",
            "",
            "If submodule clone fails due SSH access, switch to HTTPS:",
            "git config submodule.third_party/soma-retargeter.url https://github.com/NVIDIA/soma-retargeter.git",
            "git submodule sync -- third_party/soma-retargeter",
            "git submodule update --init --recursive third_party/soma-retargeter",
        ]
        if not has_submodule_files:
            lines.insert(1, f"Detected empty submodule folder: {submodule_dir}")

        stderr = (probe.stderr or "").strip()
        if stderr:
            lines.extend(["", "Import probe stderr:", stderr])

        message = "\n".join(lines)
        self._append_log("[Launcher] Retarget dependency check failed.")
        self._append_log(message)
        reply = QMessageBox.question(
            self,
            "Missing Retarget Dependency",
            message + "\n\nRun this job without rig export?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.rig_combo.setCurrentText(RIG_NONE)
            self._append_log(
                "[Launcher] Falling back to Rig Export = None for this run. "
                "Install soma_retargeter to enable G1 export."
            )
            self._update_command_preview()
            return True
        return False

    def _update_command_preview(self) -> None:
        try:
            program, args, _ = self._build_command(strict=False)
            preview = subprocess.list2cmdline([program] + args)
        except Exception as exc:  # pragma: no cover - UI fallback path
            preview = f"[Invalid command] {exc}"
        self.command_preview.setPlainText(preview)

    def _append_log(self, text: str) -> None:
        if not text:
            return
        self.log_view.appendPlainText(text)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        print(text, flush=True)

    def _start_process(self) -> None:
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            QMessageBox.warning(self, "Process Running", "A process is already running.")
            return

        try:
            program, args, expected_output = self._build_command(strict=True)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Input", str(exc))
            return
        if not self._ensure_retarget_ready(program):
            return

        output_root = Path(self.output_edit.text().strip())
        output_root.mkdir(parents=True, exist_ok=True)

        self.last_output_path = expected_output
        self.status_label.setText("Status: Running")
        self._append_log("")
        self._append_log("=" * 80)
        self._append_log(f"Working directory: {self.project_root}")
        self._append_log(f"Expected output: {expected_output}")
        self._append_log(f"$ {subprocess.list2cmdline([program] + args)}")

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(self.project_root))
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

        self._set_running(True)
        self.process.start(program, args)
        if not self.process.waitForStarted(5000):
            self._set_running(False)
            QMessageBox.critical(self, "Start Failed", "Failed to start the selected command.")

    def _stop_process(self) -> None:
        if self.process is None or self.process.state() == QProcess.NotRunning:
            return
        self._append_log("[Launcher] Stopping process...")
        self.process.terminate()
        QTimer.singleShot(5000, self._force_kill_process)

    def _force_kill_process(self) -> None:
        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self._append_log("[Launcher] Force killing process...")
            self.process.kill()

    def _read_stdout(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._stream_to_log(data, prefix="")

    def _read_stderr(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stream_to_log(data, prefix="[stderr] ")

    def _stream_to_log(self, data: str, prefix: str) -> None:
        if not data:
            return
        for line in data.replace("\r", "\n").splitlines():
            self._append_log(f"{prefix}{line}" if prefix else line)

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._set_running(False)
        if exit_code == 0:
            self.status_label.setText("Status: Completed")
            self._append_log("[Launcher] Process completed successfully.")
        else:
            self.status_label.setText(f"Status: Failed (exit code {exit_code})")
            self._append_log(f"[Launcher] Process failed with exit code {exit_code}.")

    def _process_error(self, error: QProcess.ProcessError) -> None:
        self._append_log(f"[Launcher] Process error: {error}")

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        controls = (
            self.mode_combo,
            self.video_edit,
            self.video_btn,
            self.output_edit,
            self.output_btn,
            self.ckpt_edit,
            self.ckpt_btn,
            self.rig_combo,
            self.static_cam_cb,
            self.verbose_cb,
            self.render_mhr_cb,
            self.force_pytorch_cb,
            self.no_imgfeat_cb,
            self.ddim_cb,
            self.save_raw_cb,
            self.detector_combo,
            self.conf_spin,
        )
        if running:
            for widget in controls:
                widget.setEnabled(False)
        else:
            for widget in controls:
                widget.setEnabled(True)
            self._refresh_mode_ui()

    def _open_output_folder(self) -> None:
        try:
            _, _, expected = self._build_command(strict=False)
        except Exception:
            expected = self.last_output_path
        target = expected if expected.exists() else self.last_output_path
        if not target.exists():
            QMessageBox.information(self, "Output Not Found", f"Path does not exist:\n{target}")
            return

        if sys.platform.startswith("win"):
            os.startfile(str(target))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

    def _load_settings(self) -> None:
        self.mode_combo.setCurrentText(self.settings.value("mode", MODE_FULL))
        self.video_edit.setText(self.settings.value("video", ""))
        self.output_edit.setText(
            self.settings.value("output", str(self._default_output_for_mode(self.mode_combo.currentText())))
        )
        self.ckpt_edit.setText(self.settings.value("ckpt", str(self._default_ckpt())))
        self.rig_combo.setCurrentText(self.settings.value("rig", RIG_NONE))

        self.static_cam_cb.setChecked(self._setting_bool("static_cam", False))
        self.verbose_cb.setChecked(self._setting_bool("verbose", False))
        self.render_mhr_cb.setChecked(self._setting_bool("render_mhr", False))
        self.force_pytorch_cb.setChecked(self._setting_bool("force_pytorch", False))
        self.no_imgfeat_cb.setChecked(self._setting_bool("no_imgfeat", False))
        self.ddim_cb.setChecked(self._setting_bool("ddim", False))
        self.save_raw_cb.setChecked(self._setting_bool("save_raw", False))

        self.detector_combo.setCurrentText(self.settings.value("detector", "vitdet"))
        conf_value = self.settings.value("conf_thr", 0.5)
        try:
            self.conf_spin.setValue(float(conf_value))
        except (TypeError, ValueError):
            self.conf_spin.setValue(0.5)

    def _save_settings(self) -> None:
        self.settings.setValue("mode", self.mode_combo.currentText())
        self.settings.setValue("video", self.video_edit.text().strip())
        self.settings.setValue("output", self.output_edit.text().strip())
        self.settings.setValue("ckpt", self.ckpt_edit.text().strip())
        self.settings.setValue("rig", self.rig_combo.currentText())
        self.settings.setValue("static_cam", self.static_cam_cb.isChecked())
        self.settings.setValue("verbose", self.verbose_cb.isChecked())
        self.settings.setValue("render_mhr", self.render_mhr_cb.isChecked())
        self.settings.setValue("force_pytorch", self.force_pytorch_cb.isChecked())
        self.settings.setValue("no_imgfeat", self.no_imgfeat_cb.isChecked())
        self.settings.setValue("ddim", self.ddim_cb.isChecked())
        self.settings.setValue("save_raw", self.save_raw_cb.isChecked())
        self.settings.setValue("detector", self.detector_combo.currentText())
        self.settings.setValue("conf_thr", self.conf_spin.value())

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_settings()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GEM-X Launcher")
    app.setOrganizationName("GEM-X")
    window = GemQtLauncher()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
