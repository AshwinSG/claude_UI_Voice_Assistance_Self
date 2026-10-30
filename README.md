# Voice Chat – Local Speech-to-Text Desktop App

A small desktop app built with **PySide6 (Qt)** that records your voice and transcribes it **locally** with [OpenAI Whisper](https://github.com/openai/whisper). Click **Record**, speak, then click **Stop**. Your words appear in the conversation window.

No audio leaves your machine. Transcription runs entirely offline once the model has been downloaded.

## Features

- One-click record/stop toggle
- Local speech-to-text with Whisper (`base` model by default)
- Chat-style transcript window with a **Clear** button
- Status bar showing model loading, recording and transcription state
- Model loading and transcription run on background threads, so the UI stays responsive

## Requirements

- Python 3.9+
- A working microphone
- [FFmpeg](https://ffmpeg.org/) (a Whisper dependency)
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: `choco install ffmpeg` or `winget install ffmpeg`
- Linux only: PortAudio for `sounddevice` (`sudo apt install libportaudio2`)

## Installation

```bash
git clone https://github.com/AshwinSG/claude_UI_Voice_Assistance_Self.git
cd claude_UI_Voice_Assistance_Self

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Usage

```bash
python3 app.py
```

1. Wait for the status to change from **Loading Whisper model...** to **Ready**.
2. Click **● Record** and speak.
3. Click **■ Stop**. The status shows **Transcribing...**, and then your text appears in the window as `You: ...`.
4. Click **Clear** to empty the conversation.

> **First run:** Whisper downloads the selected model (about 140 MB for `base`) and caches it in `~/.cache/whisper`.
>
> **macOS:** You will be asked to grant microphone access to Terminal or your IDE. Allow it, then restart the app if recording fails.

## Configuration

Edit the constants at the top of [`app.py`](app.py):

| Constant      | Default  | Description |
|---------------|----------|-------------|
| `MODEL_NAME`  | `"base"` | Whisper model: `tiny`, `base`, `small`, `medium`, `large`. Larger models are more accurate but slower and use more memory. |
| `SAMPLE_RATE` | `16000`  | Microphone sample rate in Hz. Whisper expects 16 kHz audio. |

## How It Works

```
Microphone ──► sounddevice InputStream ──► audio chunks (float32, 16 kHz, mono)
                                                   │  on Stop
                                                   ▼
                                     numpy concatenate ──► Whisper.transcribe()
                                                                 │ (background thread)
                                                                 ▼
                                               Qt Signal ──► chat window update
```

- **`VoiceApp`** is the main Qt window: transcript (`QTextEdit`), Record and Clear buttons, and a status label.
- **`Signals`** is a `QObject` whose Qt signals let background threads update the UI safely, because Qt widgets may only be touched from the main thread.
- **`_load_model`** loads Whisper on a background thread at startup. The Record button stays disabled until it is ready.
- **`_start_recording` / `_stop_recording`** open and close a `sounddevice` input stream. Its callback appends each audio block to a buffer.
- **`_transcribe`** runs Whisper on the captured audio on a background thread and emits the result to the chat window.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Mic error: ...` | Check that a microphone is connected and that your terminal or IDE has microphone permission. |
| `Model error: ...` | Check your internet connection on first run (model download), and that FFmpeg is installed. |
| `(no speech detected)` | Speak louder or closer to the mic, or record for longer. |
| Transcription is slow | Use a smaller model (`tiny`) or a machine with a GPU. |
| `FP16 is not supported on CPU` warning | Harmless. The app already passes `fp16=False`. |

## Project Structure

```
.
├── app.py             # The application
├── requirements.txt   # Python dependencies
├── .gitignore
└── README.md
```

## Roadmap Ideas

- Send transcribed text to an AI assistant (e.g. Claude) and show its replies in the chat
- Text-to-speech for assistant responses
- Push-to-talk hotkey
- Model selector in the UI
