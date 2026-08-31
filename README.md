# 🎧 Autonomous Local AI Audiobook & Video Summarizer

> **100% Local & Open-Source Agentic Pipeline**: Transforms YouTube playlists, videos, and local audio/video media into studio-mastered audiobooks and factual text digests using AMD GPU Vulkan Whisper, Local Ollama LLM, and Neural Speech Synthesis.

---

## 🌟 Key Highlights

- **🤖 Autonomous Agent-First Architecture**: 5 specialized local agents coordinate across ingestion, acoustic transcription, multi-turn editorial synthesis, audiobook mastering, and QA supervision.
- **⚡ AMD GPU Vulkan Acceleration**: Native **Whisper `large-v3`** acceleration on AMD Radeon GPUs (RX 6600, RDNA2/3) using Vulkan compute and Flash Attention.
- **🛡️ Anti-Looping Hallucination Shield**: Solves Whisper looping bugs permanently with zero cross-chunk prompt bleed (`-mc 0`), non-speech suppression (`-sns`), segment deduplication, and Silero VAD neural pre-filtering.
- **⚖️ Self-Evaluating Critic Loop**: An autonomous LLM Judge audits every section across 4 dimensions (*Faithfulness, Ad/Sponsor Removal, Spoken Flow, Translation Quality*), triggering automatic self-correction if scores fall below 8.0/10.
- **🎙️ Studio Audio Mastering**: Produces 192kbps MP3 audiobooks normalized to the broadcast standard **EBU R128** ($-16\text{ LUFS}$), complete with **embedded YouTube thumbnail cover art** and **clickable chapter markers**.
- **🌙 Silent Background Daemon**: "Drop-and-forget" watcher that continuously monitors `data/input/` and `playlists.txt` silently in the background.

---

## 🏗️ Architecture & Agent System

```mermaid
flowchart TD
    subgraph INGESTION ["1. Ingestion Layer"]
        YT[YouTube Playlist / Video URL] --> IA[Ingestion Agent]
        LF[Local Media in data/input/] --> IA
        IA -->|16kHz Normalized WAV + Thumbnail + Chapters| TA[Acoustic Perception Agent]
    end

    subgraph ACOUSTIC ["2. Acoustic Perception"]
        TA -->|AMD Vulkan Whisper large-v3| VAD{Silero VAD & Loop Shield}
        VAD -->|Timestamped Paragraph Transcript| EA[Editorial Agent]
    end

    subgraph EDITORIAL ["3. Multi-Turn LLM Editor (Local Ollama)"]
        EA --> G1[Section Drafter]
        G1 --> G2[Sponsor / Promo Purger]
        G2 --> G3[Spoken Narrative Polish]
        G3 --> G4[Factual Consistency Critic]
        G4 -- "Score < 8.0" --> G3
        G4 -- "PASS (Score >= 8.0)" --> AA[Audiobook Director Agent]
    end

    subgraph AUDIOBOOK ["4. Audiobook Mastering & Packaging"]
        AA --> TTS[Neural Voice Synthesis (Edge-TTS)]
        TTS --> MAST[FFmpeg EBU R128 Mastering -16 LUFS]
        MAST --> ID3[Mutagen ID3v2 Tags + Cover Art + Chapters]
        ID3 --> OUT[data/output/ + Quality Scorecard]
    end
```

---

## 📋 Prerequisites

1. **Python 3.10+** (Tested on Python 3.11 / 3.12 on Windows)
2. **FFmpeg** on system `PATH`
3. **[Ollama](https://ollama.com/)** with model pulled (default: `qwen2.5:7b` or `qwen3:14b`):
   ```powershell
   ollama pull qwen2.5:7b
   ```
4. **AMD GPU / Vulkan Drivers** (or CPU fallback)

---

## 🚀 Quick Setup

```powershell
# 1. Clone repository
git clone https://github.com/Az-har/youtube-summarizer-audiobook.git
cd youtube-summarizer-audiobook

# 2. Setup Virtual Environment
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install Dependencies
pip install -r requirements.txt

# 4. Copy Environment Template
Copy-Item .env.example .env
```

---

## 🎮 How to Run

### Mode 1: Interactive One-Shot CLI

#### Process a YouTube Playlist
```powershell
py main_pipeline.py "https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID"
```

#### Process a Single Video
```powershell
py main_pipeline.py "https://www.youtube.com/watch?v=YOUR_VIDEO_ID"
```

#### Process URLs from `playlists.txt`
Add YouTube links to `playlists.txt` (one per line), then run:
```powershell
py main_pipeline.py
```

#### Test Neural Voice Output
```powershell
py main_pipeline.py --test-tts --language English
py main_pipeline.py --test-tts --language Tamil
```

---

### Mode 2: Autonomous Silent Background Daemon

Run the daemon to monitor folder drops and playlist queues with zero intervention:

```powershell
# Start daemon worker loop
py daemon.py --start
```

- **Drop any media files** (`.mp3`, `.wav`, `.m4a`, `.mp4`, `.mkv`, `.webm`) into `data/input/` or add URLs to `playlists.txt`.
- The daemon detects them automatically, queues them, executes the full self-healing agent workflow, and delivers finished audiobooks to `data/output/`.

#### Check Active Tasks & Metrics
```powershell
py daemon.py --status
```

---

## 📁 Output Structure (`data/output/`)

```text
data/output/
  ├── audiobooks/
  │     └── <Title>.mp3         # Mastered 192kbps MP3 with embedded cover art & chapters
  ├── summaries/
  │     └── <Title>.md          # Clean Markdown summary & key insights
  ├── transcripts/
  │     └── <Title>.txt         # Timestamped clean paragraph transcript
  └── reports/
        └── <VideoID>_quality.json  # Full QA evaluator scorecard (Scores: 1-10)
```

---

## 🏆 Quality Valuation Scorecard

At the end of every video, a terminal quality badge and itemized report are emitted:

```text
=======================================================
🏆 AGENT QUALITY SCORECARD: PASS (Score: 9.6/10)
  - Ingestion       : PASS [Score: 10.0/10]
  - Transcription   : PASS [Score: 9.7/10]
  - Summarization   : PASS [Score: 9.3/10]
  - Tts_audio       : PASS [Score: 9.5/10]
=======================================================
```

---

## 🧪 Testing

Run the full automated test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

---

## 📜 License

MIT License. Crafted for high-performance local AI audio engineering.
