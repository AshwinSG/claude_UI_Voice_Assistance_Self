"""
Voice Chat App - PySide6 (Qt) desktop app + Whisper speech-to-text

Click "Record", speak, click "Stop". Your speech is transcribed
locally with OpenAI Whisper and added to the conversation window.

Type text and press "Speak" to hear Mira say it with a local Piper voice.

Mira, the on-screen character, listens while you speak, talks while the
voice plays, and idles otherwise.

Setup (once):
    pip3 install -r requirements.txt

Run:
    python3 app.py

Notes:
- The first run downloads the Whisper model (~140 MB for "base") and the
  Piper voice (~115 MB, saved in models/).
- macOS will ask for microphone permission for Terminal / your IDE. Allow it.
"""

import html
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import sounddevice as sd
import whisper
from piper import PiperVoice
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout,
    QWidget,
)

SAMPLE_RATE = 16000
MODEL_NAME = "base"

VOICE_NAME = "en_US-ljspeech-high"  # Female, American English
VOICE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high/"
)
VOICE_DIR = Path(__file__).parent / "models"

SPRITE_DIR = Path(__file__).parent / "mira_sprites"
SPRITE_SCALE = 4
SPEECH_THRESHOLD = 0.01  # Mic RMS level above which you count as speaking
SPEECH_HOLD_SECONDS = 0.6  # Keep listening through short pauses between words


# Background threads must not touch widgets directly; they emit these signals.
class Signals(QObject):
    status = Signal(str)
    chat = Signal(str, str)
    ready = Signal()
    loaded = Signal()
    speaking = Signal(bool)
    talking = Signal(bool)


# Plays Mira's animations from the sprite sheet: "idle", "listen" or "talk".
class MiraSprite(QLabel):
    def __init__(self):
        super().__init__(alignment=Qt.AlignCenter)
        sheet = QPixmap(str(SPRITE_DIR / "mira_spritesheet.png"))
        meta = json.loads((SPRITE_DIR / "mira_spritesheet.json").read_text())

        self.animations = {}
        for name, anim in meta["animations"].items():
            frames = []
            for f in anim["frames"]:
                frame = sheet.copy(f["x"], f["y"], f["w"], f["h"])
                # FastTransformation keeps the pixel art crisp.
                frames.append(frame.scaled(
                    f["w"] * SPRITE_SCALE, f["h"] * SPRITE_SCALE,
                    mode=Qt.FastTransformation,
                ))
            self.animations[name] = (frames, anim["fps"])

        self.setFixedHeight(meta["frame_height"] * SPRITE_SCALE)
        self.current = None
        self.frame_index = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.play("idle")

    def play(self, name: str):
        if name == self.current:
            return
        self.current = name
        self.frame_index = 0
        frames, fps = self.animations[name]
        self.setPixmap(frames[0])
        self.timer.start(1000 // fps)

    def _next_frame(self):
        frames, _ = self.animations[self.current]
        self.frame_index = (self.frame_index + 1) % len(frames)
        self.setPixmap(frames[self.frame_index])


class VoiceApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Chat")
        self.resize(520, 680)

        self.model = None
        self.voice = None
        self.busy = False  # Transcribing or speaking
        self.recording = False
        self.stream = None
        self.audio_chunks: list[np.ndarray] = []
        self.speaking = False
        self.last_voice_time = 0.0

        self.signals = Signals()
        self.signals.status.connect(self._set_status)
        self.signals.chat.connect(self._append_chat)
        self.signals.ready.connect(self._on_ready)
        self.signals.loaded.connect(self._on_loaded)
        self.signals.speaking.connect(self._update_mira)
        self.signals.talking.connect(self._on_talking)

        self._build_ui()
        self._set_status("Loading models...")
        self._update_controls()
        threading.Thread(target=self._load_model, daemon=True).start()
        threading.Thread(target=self._load_voice, daemon=True).start()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.mira = MiraSprite()
        layout.addWidget(self.mira)

        self.chat = QTextEdit(readOnly=True)
        self.chat.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.chat)

        speak_row = QHBoxLayout()
        self.text_input = QLineEdit(placeholderText="Type something for Mira to say...")
        self.text_input.returnPressed.connect(self.speak)
        speak_row.addWidget(self.text_input)
        self.speak_btn = QPushButton("Speak")
        self.speak_btn.clicked.connect(self.speak)
        speak_row.addWidget(self.speak_btn)
        layout.addLayout(speak_row)

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

    def _update_controls(self):
        self.record_btn.setEnabled(self.model is not None and not self.busy)
        self.speak_btn.setEnabled(
            self.voice is not None and not self.busy and not self.recording
        )

    def _on_ready(self):
        self.busy = False
        self._update_controls()

    def _on_loaded(self):
        self._update_controls()
        if self.model is not None and self.voice is not None:
            self._set_status("Ready")

    def _on_talking(self, talking: bool):
        self.mira.play("talk" if talking else "idle")

    def _update_mira(self, speaking: bool):
        # A queued signal can arrive after Stop, so also check we're recording.
        self.mira.play("listen" if speaking and self.recording else "idle")

    # ---------- Model ----------
    def _load_model(self):
        try:
            self.model = whisper.load_model(MODEL_NAME)
            self.signals.loaded.emit()
        except Exception as e:
            self.signals.status.emit(f"Model error: {e}")

    def _load_voice(self):
        try:
            VOICE_DIR.mkdir(exist_ok=True)
            # The .onnx comes last: its presence means the download finished.
            for name in (f"{VOICE_NAME}.onnx.json", f"{VOICE_NAME}.onnx"):
                path = VOICE_DIR / name
                if not path.exists():
                    self.signals.status.emit("Downloading voice...")
                    partial = path.with_suffix(".part")
                    urllib.request.urlretrieve(VOICE_URL + name, partial)
                    partial.rename(path)
            self.voice = PiperVoice.load(VOICE_DIR / f"{VOICE_NAME}.onnx")
            self.signals.loaded.emit()
        except Exception as e:
            self.signals.status.emit(f"Voice error: {e}")

    # ---------- Speech ----------
    def speak(self):
        text = self.text_input.text().strip()
        if not text or not self.speak_btn.isEnabled():
            return
        self.text_input.clear()
        self._append_chat("Mira", text)
        self.busy = True
        self._update_controls()
        self._set_status("Generating speech...")
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _speak(self, text: str):
        try:
            chunks = list(self.voice.synthesize(text))
            if not chunks:
                self.signals.status.emit("Nothing to say")
                return
            audio = np.concatenate([c.audio_float_array for c in chunks])
            self.signals.talking.emit(True)
            self.signals.status.emit("Speaking...")
            sd.play(audio, chunks[0].sample_rate)
            sd.wait()
            self.signals.status.emit("Ready")
        except Exception as e:
            self.signals.status.emit(f"Speech error: {e}")
        finally:
            self.signals.talking.emit(False)
            self.signals.ready.emit()

    # ---------- Recording ----------
    def toggle_recording(self):
        if self.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _audio_callback(self, indata, frames, time_info, status):
        self.audio_chunks.append(indata.copy())

        now = time.monotonic()
        if np.sqrt(np.mean(indata ** 2)) > SPEECH_THRESHOLD:
            self.last_voice_time = now
        speaking = now - self.last_voice_time < SPEECH_HOLD_SECONDS
        if speaking != self.speaking:
            self.speaking = speaking
            self.signals.speaking.emit(speaking)

    def _start_recording(self):
        self.audio_chunks = []
        self.speaking = False
        self.last_voice_time = 0.0
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
        self._update_controls()
        self.record_btn.setText("■ Stop")
        self._set_status("Recording...")

    def _stop_recording(self):
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self._update_mira(False)
        self.record_btn.setText("● Record")

        if not self.audio_chunks:
            self._set_status("No audio captured")
            self._update_controls()
            return

        self.busy = True
        self._update_controls()
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
        sd.stop()
        if self.stream:
            self.stream.stop()
            self.stream.close()
        event.accept()


if __name__ == "__main__":
    qt_app = QApplication(sys.argv)
    window = VoiceApp()
    window.show()
    sys.exit(qt_app.exec())
