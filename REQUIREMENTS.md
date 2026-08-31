# YouTube Audio Digest — Requirements and Build Plan

## 1. Purpose

Build a weekly automation that reads selected YouTube sources, finds new videos, removes non-content portions, produces a spoken version in the source-output language, and publishes each result as a private episode in a YouTube Podcast playlist.

The output is intended for the account owner’s private listening. The system processes at most **10 new videos per run**.

## 2. Confirmed requirements

### Sources

- The owner maintains one editable source file containing YouTube channel, playlist, and individual-video URLs.
- The owner’s own playlist has the highest priority.
- Other sources are prioritized by category; the initial category order is:
  1. `my_playlist`
  2. `health`
  3. `tech`
- A source can be a channel, playlist, or individual video.
- Only videos not already recorded as completed are eligible.
- If the owner removes a completed item from the completion CSV, it becomes eligible again and will be processed in a later run.
- At equal priority, newly added videos from the owner’s playlist come first. For the remaining ties, process the oldest eligible video first so that videos are not permanently skipped.

### Language and transcript rules

- If the detected source language is Tamil, the final narration language is Tamil.
- Every non-Tamil source language, including English, has an English final narration.
- Transcribe source audio locally with the open-source Whisper model, run through `faster-whisper` using the `large-v3` model. This is the default transcript source; it does not send source audio to an OpenAI transcription API.
- When YouTube captions exist, retain them only as an optional comparison/reference artifact; they do not replace the local Whisper transcription.
- If a source language is neither Tamil nor English, translate the retained content to English before narration.

### Content rules

- Remove non-information material, including intros, outros, sponsor reads, adverts, calls to subscribe/like/comment, repeated prompts, promotional material, unrelated banter, and similar filler.
- Preserve the substantive information, explanations, examples, caveats, conclusions, and useful context.
- Videos **shorter than 20 minutes**: create a cleaned, near-complete read-aloud narration in the source-output language.
- Videos **20 minutes or longer**: create a detailed information-first synthesis in the source-output language. Output length is not a constraint.
- The system must report uncertain removals or low-confidence transcription rather than silently pretending that all information was preserved.

### Output and publishing

- Render each narration as an audio file and a simple video file using a static cover image; YouTube video uploads require a video container.
- Upload each result as **Private** to the owner’s YouTube channel.
- Add it to one private YouTube Podcast playlist so it can be used as a podcast-style private library and can be eligible for YouTube Music podcast features.
- Each episode includes the source title, source URL, source channel, processing date, duration rule used, and any quality warnings in its description.
- Store local artifacts (transcript, cleaned text, final narration script, audio, video, metadata) so an upload can be retried without re-transcribing.

### Operations

- Run weekly. The exact day/time is deliberately deferred; the schedule must be configurable (initially Thursday or Saturday).
- No more than 10 videos are completed in one run.
- Each run produces a report: discovered, selected, skipped as completed, skipped as not permitted, completed, failed, and items needing review.
- Use a local open-source TTS adapter. The exact Tamil and English model/voice are intentionally deferred until the framework is complete and sample quality has been evaluated. No cloud TTS account or paid TTS service is required.

## 3. Rights and safety boundary

The automation may process and upload only source material the owner is entitled to transform and upload. Each source must explicitly declare this in the source file.

- `permitted: true`: eligible for processing.
- `permitted: false`: discover and report only; do not download, transcribe, translate, narrate, or upload it.

This avoids accidentally republishing third-party work, even privately.

## 4. Data files

### `config/sources.csv`

The owner edits this file. One row represents one source.

```csv
source_id,kind,url,category,priority,permitted,enabled,notes
my_saved,playlist,https://www.youtube.com/playlist?list=...,my_playlist,1,true,true,Primary personal playlist
health_updates,channel,https://www.youtube.com/@example,health,2,true,true,
tech_watch,playlist,https://www.youtube.com/playlist?list=...,tech,3,false,true,Discover only until rights are confirmed
```

Rules:

- `source_id` is a stable short identifier selected by the owner.
- `kind` is `channel`, `playlist`, or `video`.
- Lower `priority` wins.
- The category documents why a source has its priority; priority is the actual sort key.
- A disabled source is ignored without changing history.

### `data/completed_videos.csv`

The automation reads this file at the beginning of every run. It is intentionally human-editable. Deleting a row is the reprocessing mechanism.

```csv
video_id,source_id,source_title,source_url,published_at,processed_at,output_video_id,output_url,mode,transcript_method,status
abc123,my_saved,Example title,https://www.youtube.com/watch?v=abc123,2026-08-20T10:00:00Z,2026-08-22T09:00:00Z,xyz789,https://youtu.be/xyz789,clean_readaloud,local_whisper_large_v3,completed
```

`video_id`, rather than the title, is the duplicate-prevention key because titles may repeat or change. Titles remain in the CSV for readability.

### Other generated data

```text
data/runs/<run-id>/report.json
data/videos/<youtube-video-id>/metadata.json
data/videos/<youtube-video-id>/transcript.*
data/videos/<youtube-video-id>/cleaned_source.txt
data/videos/<youtube-video-id>/narration_script.txt
data/videos/<youtube-video-id>/narration.mp3
data/videos/<youtube-video-id>/episode.mp4
```

## 5. Selection algorithm

1. Read enabled sources from `config/sources.csv`.
2. Discover their videos through the YouTube Data API.
3. Merge duplicate discoveries by YouTube video ID.
4. Ignore videos with a `video_id` in `data/completed_videos.csv`.
5. Exclude sources marked `permitted: false`, but list them in the report.
6. Sort candidates by:
   - source priority (ascending),
   - owner-playlist recency (newest first),
   - publication time (oldest first for all other ties),
   - video ID as a final deterministic tie-breaker.
7. Select the first 10 videos.
8. Process each selected video independently; a failure must not prevent the remaining videos from running.
9. Write a completed CSV row only after a private YouTube upload and podcast-playlist insertion both succeed.

## 6. Per-video pipeline

```text
Discover → permissions check → local Whisper transcription
        → local Whisper transcript → language detection → remove non-content sections
        → translate where required → apply duration rule
        → Tamil or English narration → static-cover video render
        → private YouTube upload → add to private Podcast playlist
        → record completion and report result
```

Detailed behavior:

1. Fetch title, description, channel, upload date, duration, and captions availability.
2. Obtain a local Whisper audio transcription. If available, retain captions as a comparison artifact.
3. Preserve timestamped transcript segments for audit and quality review.
4. Identify/removes non-content ranges, retaining the reasons and timestamps in `metadata.json`.
5. Apply the language rule: Tamil source → Tamil narration; every other language → English narration. Translate retained information only when the source and output language differ, maintaining names, figures, claims, source references, and uncertainty.
6. Choose mode using original video duration:
   - `< 20:00`: `clean_readaloud`
   - `>= 20:00`: `detailed_synthesis`
7. Generate a Tamil or English narration script and render it through the configured local open-source TTS provider.
8. Create an MP4 episode from narration audio + static square cover image.
9. Upload privately, set metadata, and add it to the destination Podcast playlist.
10. Save outputs and mark completed only after all required operations succeed.

## 7. Technology plan

### Application

- Python 3.11+ application, starting from `main_pipeline.py`.
- `pydantic` for configuration/data validation.
- `httpx` or the official Google client libraries for APIs.
- SQLite can be added later for richer run history, but the owner-facing completion authority remains the editable CSV.

### YouTube

- Google OAuth 2.0 for the owner’s account.
- YouTube Data API for channel/playlist discovery, metadata, uploads, and playlist membership.
- A verified OAuth/API setup is required for reliable private uploads. Store client secrets and refresh tokens outside version control.
- A single pre-created private Podcast playlist is configured by ID. The owner enables podcast features in YouTube Studio after creation if required.

### AI services

- **Local transcription:** `faster-whisper` + Whisper `large-v3`. It runs on the machine and is the only transcription engine. A CUDA-capable NVIDIA GPU is strongly recommended; CPU operation is supported but much slower.
- **Content editor and translator:** local Ollama model by default, initially `qwen3:14b` (or a larger configured Qwen model if the machine GPU memory permits). It returns structured sections, removal reasons, confidence, and the required Tamil/English narration script. A cloud LLM is not required in the first build.
- **Narration:** a local open-source TTS adapter. It accepts a narration script, language code, voice/model ID, and chunking settings, then returns WAV/MP3 audio. Its initial implementation is deliberately selected only after the rest of the framework works: evaluate Tamil samples first, then English samples, and retain the best acceptable local models. The adapter is isolated so a later model replacement does not alter discovery, transcript, content, rendering, or publishing behavior.

### Media handling

- `ffmpeg` for audio normalization and static-cover MP4 rendering.
- Avoid retaining original source-video downloads after successful processing unless the owner later requests archival.

### Scheduling

- A command such as `python main_pipeline.py run` performs one complete run.
- Schedule it later with Windows Task Scheduler, GitHub Actions, or a Codex/ChatGPT automation. Schedule data lives in configuration, not code.

## 8. Configuration and secrets

Example `.env` keys (never commit this file):

```env
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
YOUTUBE_DESTINATION_CHANNEL_ID=
YOUTUBE_PODCAST_PLAYLIST_ID=
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:14b
WHISPER_MODEL=large-v3
TTS_PROVIDER=local_open_source
TTS_MODEL=
TTS_VOICE_TAMIL=
TTS_VOICE_ENGLISH=
```

Required user setup before the upload phase:

1. Create/select the destination YouTube channel.
2. Create a private playlist for the audio digest and enable Podcast features in YouTube Studio if desired.
3. Create Google Cloud OAuth credentials and authorize the channel.
4. Install Ollama, download the configured Qwen model, and install the local Whisper runtime/model.
5. After the framework is complete, install the selected local open-source TTS model and choose one Tamil and one English voice during the pilot.
6. Add permitted sources to `config/sources.csv`.

## 8A. Implementation status (current)

- Implemented: validated editable source/completion CSVs, deterministic priority selection, local artifact storage, YouTube discovery/publishing integration, local Whisper integration, local Ollama content processing, chunking for long transcripts, local-TTS command adapter, MP4 rendering, JSON run reports, and offline unit tests for selection rules.
- Deferred by design: installation and voice-quality evaluation of the final local open-source Tamil/English TTS models; weekly schedule selection; real Google OAuth credentials and source URLs.
- Environment prerequisite: Python, ffmpeg, yt-dlp, Ollama, the required local models, and Google OAuth credentials must be installed/configured before a real run.

## 9. Build phases

### Phase 1 — Framework and dry run

- Create project structure, settings validation, source/completion CSV handlers, run reports, logging, and CLI commands.
- Implement YouTube discovery and the exact 10-video selection logic.
- Add `--dry-run` to show what would be processed without downloading, calling AI services, or uploading.

### Phase 2 — Content preparation

- Add local Whisper transcription and optional caption comparison.
- Add language detection, timestamped transcript storage, removal-review data, Tamil/English translation, and duration-mode logic.
- Test with one permitted short video and one permitted long video.

### Phase 3 — Narration voice pilot

- Generate short Tamil samples using candidate local open-source TTS models/voices, then test English candidates.
- Owner selects one voice per language for intelligibility and preferred style.
- Lock the selected local models and voices in configuration.

### Phase 4 — Publishing

- Generate cover image/MP4 episodes.
- Upload them privately, add them to the Podcast playlist, and save published URLs.
- Verify retry behavior so an interrupted run cannot create duplicate uploads.

### Phase 5 — Weekly operation

- Configure the Thursday/Saturday schedule when the owner chooses one.
- Run the first monitored batch, review its report and private episodes, then adjust prompts/voice before routine unattended use.

## 10. Acceptance criteria

- A dry run correctly discovers sources and selects no more than 10 eligible permitted videos.
- Items in the completed CSV are skipped; deleting their rows makes them eligible again.
- A Tamil short video yields a cleaned near-complete Tamil narration; an English short video yields a cleaned near-complete English narration.
- A long video yields a detailed information-first narration in Tamil only when its source is Tamil; otherwise it is in English.
- Every selected video receives a local Whisper transcription, independent of caption availability.
- Completed episodes are private and present in the configured Podcast playlist.
- A failed item does not block the other selected items.
- Each run produces a readable report with reasons for skips, failures, and quality warnings.
