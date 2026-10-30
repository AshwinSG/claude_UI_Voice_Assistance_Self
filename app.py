"""
Voice Chat App - PySide6 (Qt) desktop app + Whisper speech-to-text

Click "Record", speak, click "Stop". Your speech is transcribed
locally with OpenAI Whisper and added to the conversation window.

Setup (once):
    pip3 install openai-whisper sounddevice numpy PySide6

Run:
    python3 app.py

Notes:
- The first run downloads the Whisper model (~140 MB for "base").
- macOS will ask for microphone permission for Terminal / your IDE. Allow it.
"""

import html
import sys
import threading

import numpy as np
import sounddevice as sd
import whisper
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

SAMPLE_RATE = 16000
MODEL_NAME = "base"


# Background threads must not touch widgets directly; they emit these signals.
class Signals(QObject):
    status = Signal(str)
    chat = Signal(str, str)
    ready = Signal()


class VoiceApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Chat")
        self.resize(520, 460)

        self.model = None
        self.recording = False
        self.stream = None
        self.audio_chunks: list[np.ndarray] = []

        self.signals = Signals()
        self.signals.status.connect(self._set_status)
        self.signals.chat.connect(self._append_chat)
        self.signals.ready.connect(lambda: self.record_btn.setEnabled(True))

        self._build_ui()
        self._set_status("Loading Whisper model...")
        self.record_btn.setEnabled(False)
        threading.Thread(target=self._load_model, daemon=True).start()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.chat = QTextEdit(readOnly=True)
        self.chat.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.chat)

        controls = QHBoxLayout()
        self.record_btn = QPushButton("● Record")
        self.record_btn.clicked.connect(self.toggle_recording)
        controls.addWidget(self.record_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.chat.clear)
        controls.addWidget(clear_btn)

        controls.addStretch()
        self.status = QLabel()
        controls.addWidget(self.status)
        layout.addLayout(controls)

    def _set_status(self, text: str):
        self.status.setText(text)

    def _append_chat(self, speaker: str, text: str):
        self.chat.append(
            f'<p><b style="color:#1a5fb4">{html.escape(speaker)}:</b> {html.escape(text)}</p>'
        )

    # ---------- Model ----------
    def _load_model(self):
        try:
            self.model = whisper.load_model(MODEL_NAME)
            self.signals.status.emit("Ready")
            self.signals.ready.emit()
        except Exception as e:
            self.signals.status.emit(f"Model error: {e}")

    # ---------- Recording ----------
    def toggle_recording(self):
        if self.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _audio_callback(self, indata, frames, time_info, status):
        self.audio_chunks.append(indata.copy())

    def _start_recording(self):
        self.audio_chunks = []
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                callback=self._audio_callback,
            )
            self.stream.start()
        except Exception as e:
            self._set_status(f"Mic error: {e}")
            return
        self.recording = True
        self.record_btn.setText("■ Stop")
        self._set_status("Recording...")

    def _stop_recording(self):
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.record_btn.setText("● Record")
        self.record_btn.setEnabled(False)

        if not self.audio_chunks:
            self._set_status("No audio captured")
            self.record_btn.setEnabled(True)
            return

        audio = np.concatenate(self.audio_chunks).flatten()
        self._set_status("Transcribing...")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio: np.ndarray):
        try:
            result = self.model.transcribe(audio, fp16=False)
            text = result["text"].strip() or "(no speech detected)"
            self.signals.chat.emit("You", text)
            self.signals.status.emit("Ready")
        except Exception as e:
            self.signals.status.emit(f"Transcription error: {e}")
        finally:
            self.signals.ready.emit()

    def closeEvent(self, event):
        if self.stream:
            self.stream.stop()
            self.stream.close()
        event.accept()


if __name__ == "__main__":
    qt_app = QApplication(sys.argv)
    window = VoiceApp()
    window.show()
    sys.exit(qt_app.exec())
