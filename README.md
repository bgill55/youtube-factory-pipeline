# YouTube Factory Pipeline

**Modular, provider-agnostic agents for automated YouTube video production.**

[![PyPI](https://img.shields.io/pypi/v/youtube-factory-pipeline.svg)](https://pypi.org/project/youtube-factory-pipeline/)
[![Python](https://img.shields.io/pypi/pyversions/youtube-factory-pipeline.svg)](https://pypi.org/project/youtube-factory-pipeline/)
[![License](https://img.shields.io/pypi/l/youtube-factory-pipeline.svg)](https://pypi.org/project/youtube-factory-pipeline/)

---

## Overview

`youtube-factory-pipeline` is the **core orchestration library** behind the "Weight and See" YouTube channel. It provides a clean, modular set of agents that handle every stage of video production:

```
Research → Idea Generation → Script Writing → Voiceover → Visuals → 
Audio Generation → Assembly → Shorts → Upload → Guide Deployment
```

Each agent is **independently usable**, **provider-agnostic**, and **configured via a single JSON file**.

---

## Features

| Agent | Purpose | Key Capability |
|-------|---------|----------------|
| `ResearchAgent` | GitHub/HF/arXiv/web/YouTube/RSS/semantic search | Structured JSON briefs via Agent Reach (yt-dlp, Jina Reader, gh CLI, Exa MCP, feedparser) |
| `VideoAnalysisAgent` | Screen recording analysis | FFmpeg scenes + Whisper + Tesseract OCR (8 classes) |
| `ScriptBuilderAgent` | Asset-First script construction | Groups OCR segments → builds scenes with explicit timestamps |
| `IdeaGeneratorAgent` | Video concept + SWOT | 3 concepts → 1 selected, niche diversity enforced |
| `ScriptwriterAgent` | 3-act retention scripts | Hook → Breakdown → Deep Dive + CTA |
| `VoiceoverAgent` | TTS via OmniVoice/Edge/Supertonic | Multi-speaker, word-level timestamps, CPU-only Supertonic M4 |
| `VisualAssetAgent` | B-roll + AI images + thumbnails | Pexels / SD / Fal / Pollinations / local |
| `AudioGeneratorAgent` | BGM via Stable Audio 3 | Ducking, volume automation |
| `VideoAssemblerAgent` | FFmpeg assembly | Subtitles, ducking, 4K output |
| `CommentAgent` | YouTube comment management | Reply, moderate, analyze sentiment |
| `ShortGenerator` | 9:16 vertical clips | Auto-scene selection ≤58s |
| `UploaderAgent` | YouTube Data API v3 | Playlists, thumbnails, Shorts |
| `GuideGeneratorAgent` | HTML resource pages | SEO-optimized, deployable to GitHub Pages |
| `CommunityPostAgent` | YouTube Community posts | Auto-generated from video outputs |

---

## Installation

```bash
# Core dependencies only
pip install youtube-factory-pipeline

# With optional providers
pip install "youtube-factory-pipeline[omnivoice]"      # Local TTS
pip install "youtube-factory-pipeline[stable-audio]"  # BGM generation
pip install "youtube-factory-pipeline[all]"           # Everything
```

---

## Quickstart

```python
from youtube_factory.orchestrator import PipelineOrchestrator
from youtube_factory.config import load_config

# Load your config (see Configuration below)
config = load_config("path/to/config.json")

# Create orchestrator
orchestrator = PipelineOrchestrator(workspace_dir="~/youtube_factory")

# Start a run
run_id, state = orchestrator.create_new_run(
    topic_seed="Ollama 0.9.0 local LLM benchmark",
    target_audience="Tech enthusiasts",
    competitor_analysis="Existing LM Studio reviews"
)

# Execute full pipeline (runs in background thread)
orchestrator.execute(run_id)

# Or run individual stages
from youtube_factory.agents.idea import IdeaGeneratorAgent
idea_agent = IdeaGeneratorAgent(config)
result = idea_agent.run({
    "topic_seed": "...",
    "target_audience": "...",
    "competitor_analysis": "..."
})
```

---

## Configuration

All agents read from a single `config.json` with environment variable interpolation (`${VAR}`):

```json
{
  "freellmapi": {
    "api_key": "${FREELLAPI_KEY}",
    "base_url": "http://localhost:3001/v1",
    "timeout": 120
  },
  "omnivoice": {
    "base_url": "http://localhost:3900/v1",
    "voice_id": "brian_ref"
  },
  "supertonic": {
    "base_url": "http://127.0.0.1:7788",
    "voice_name": "M4",
    "total_steps": 8,
    "speed": 1.05
  },
  "pexels_api_key": "${PEXELS_API_KEY}",
  "local_sd": {
    "base_url": "http://127.0.0.1:7860",
    "lora_url": "https://.../style.safetensors"
  },
  "video_settings": { "width": 1920, "height": 1080 },
  "upload_settings": { "privacy_status": "private" }
}
```

Environment variables loaded from `.env`:
```bash
FREELLAPI_KEY=freellmapi-xxx
PEXELS_API_KEY=xxx
GEMINI_API_KEY=xxx
# etc.
```

---

## Architecture

### Standard Pipeline
```
Research → SCRAPE → IDEA_GEN → SCRIPTWRITE → GUIDE_GEN → 
VOICEOVER → VISUALS → AUDIO_GEN → ASSEMBLY → SHORTS → GUIDE_DEPLOY → UPLOAD
```

### Asset-First Pipeline (v0.3.0+)
```
VIDEO_ANALYSIS → SCRIPT_BUILD → GUIDE → VOICEOVER → 
VISUALS → AUDIO_GEN → ASSEMBLY → SHORTS → GUIDE_DEPLOY → UPLOAD
```

```text
┌─────────────────────────────────────────────────────────────┐
│                    PipelineOrchestrator                       │
│  (state management, cancellation, threading, config reload)  │
└──────────────────────────┬──────────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Research   │    │   Scraper   │    │   Idea Gen  │
│  (LLM)      │    │  (HTTP)     │    │  (LLM+JSON) │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│                      Scriptwriter (LLM)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Voiceover  │    │  Visuals    │    │    Guide    │
│  (Edge/OV)  │    │ (Pexels/SD) │    │  (LLM+HTML) │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Audio Gen   │    │  Assembly   │    │  Guide      │
│ (Stable     │    │ (FFmpeg)    │    │  Deploy     │
│  Audio 3)   │    │             │    │  (GitHub)   │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Shorts    │    │  Upload     │    │ Community   │
│  (Scene     │    │ (YouTube)   │    │  Posts      │
│  select)    │    │             │    │             │
└─────────────┘    └─────────────┘    └─────────────┘
```

### Asset-First Pipeline Flow
```
                    ┌─────────────────────────┐
                    │    Upload Recording     │
                    └───────────┬─────────────┘
                                ▼
                    ┌─────────────────────────┐
                    │   VIDEO_ANALYSIS        │
                    │  • FFmpeg scene detect  │
                    │  • Whisper transcription│
                    │  • Tesseract OCR (8cls) │
                    └───────────┬─────────────┘
                                ▼
                    ┌─────────────────────────┐
                    │   SCRIPT_BUILD          │
                    │  • Group OCR segments   │
                    │  • Build scenes w/      │
                    │    explicit timestamps  │
                    │  • LLM narration        │
                    └───────────┬─────────────┘
                                ▼
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
       ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
       │  Voiceover  │ │  Visuals    │ │    Guide    │
       │  (Supertonic)│ │ (Asset tags │ │  (LLM+HTML) │
       │             │ │  + B-roll)  │ │             │
       └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
              │               │               │
              ▼               ▼               ▼
       ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
       │ Audio Gen   │ │ Assembly    │ │ Guide       │
       │ (Stable     │ │ (FFmpeg:    │ │ Deploy      │
       │  Audio 3)   │ │  44.1kHz/   │ │ (GitHub)    │
       │             │ │  192k)      │ │             │
       └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
              │               │               │
              ▼               ▼               ▼
       ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
       │   Shorts    │ │  Upload     │ │ Community   │
       │  (Scene     │ │ (YouTube)   │ │  Posts      │
       │  select)    │ │             │ │             │
       └─────────────┘ └─────────────┘ └─────────────┘
```

**All LLM calls route through FreeLLMAPI** → automatic provider failover, penalty tracking, system prompt injection.

---

## Agent Reach Integration (v0.2.0+)

The `ResearchAgent` now leverages **Agent Reach** — a capability layer giving agents unified, zero-API-cost access to internet sources:

| Source | Tool | Method |
|--------|------|--------|
| **YouTube** | `yt-dlp` | Transcript + metadata extraction |
| **Web Articles** | Jina Reader (`curl r.jina.ai/URL`) | Clean article extraction |
| **GitHub** | `gh CLI` | Repo metadata + README |
| **Semantic Search** | `mcporter` + Exa MCP | Neural web search |
| **RSS/Atom** | `feedparser` | Feed parsing |

**No API keys required** for any of the above. The tools are installed as system dependencies (see Installation).

### External Dependencies (install once)

```bash
# Windows (via winget/choco)
winget install GitHub.cli
npm install -g mcporter
mcporter config add exa https://mcp.exa.ai/mcp

# Python deps installed automatically with package
# yt-dlp, feedparser, agent-reach
```

### Research Agent Capabilities

```python
from youtube_factory.agents.research import ResearchAgent

agent = ResearchAgent(config)

# YouTube transcript
result = agent.run({"topic_seed": "https://youtube.com/watch?v=...", "run_dir": "/tmp/run"})

# GitHub repo deep-dive
result = agent.run({"topic_seed": "https://github.com/owner/repo", "run_dir": "/tmp/run"})

# Web article (Jina Reader)
result = agent.run({"topic_seed": "https://anthropic.com/news/...", "run_dir": "/tmp/run"})

# Semantic search (Exa)
# (Triggered automatically for generic queries in future versions)
```

The agent auto-detects the source type from the URL and routes to the appropriate backend.

---

## Asset-First Mode (v0.3.0+)

**Build videos around your screen recordings** — instead of inserting recordings into pre-existing scripts, the pipeline **analyzes your recording first** and builds the entire video around it.

### How It Works

```
Upload Screen Recording → VIDEO_ANALYSIS → SCRIPT_BUILD → VOICEOVER → VISUALS → AUDIO_GEN → ASSEMBLY → SHORTS → UPLOAD
```

| Stage | Purpose |
|-------|---------|
| `VideoAnalysisAgent` | FFmpeg scene detection + Whisper transcription + Tesseract OCR classification |
| `ScriptBuilderAgent` | Groups segments → builds scenes with explicit asset timestamps |
| `VoiceoverAgent` | Supertonic TTS with expression tags |
| `VisualAssetAgent` | Explicit asset tags + B-roll fallback |

### OCR Classification

The `VideoAnalysisAgent` classifies each frame segment:
- `terminal` — Command line / shell
- `code` — IDE / editor  
- `browser` — Web pages
- `ide` — Development environment
- `ui` — Application interfaces
- `demo` — Live demonstrations
- `explanation` — Talking head / explanations
- `visual` — Pure visual content
- `talking` — General speech

### Explicit Asset Tags

Write tags directly in visual descriptions for precise placement:

```markdown
[Visual: asset:video:assets/recording.mp4 timestamp=0-30]
[Visual: asset:video:assets/recording.mp4 timestamp=30-60]
[Visual: asset:image:assets/screenshot.png]
[Visual: asset:screenshot:https://github.com/user/repo]
```

### Usage

```python
from youtube_factory.orchestrator import PipelineOrchestrator
from youtube_factory.config import load_config

config = load_config("config.json")
orchestrator = PipelineOrchestrator(workspace_dir="~/youtube_factory")

# Asset-First run: pass asset info in initial creation
run_id, state = orchestrator.create_new_run(
    topic_seed="YouTube Factory 1.0 Demo",
    target_audience="Tech creators",
    competitor_analysis="Pictory, Runway",
    asset_first=True,
    asset_video="recording.mp4",
    asset_video_path="~/youtube_factory/workspace/runs/run_xxx/assets/recording.mp4"
)

orchestrator.execute(run_id)
```

Or via CLI:
```bash
python -m youtube_factory.run_factory \
  --seed "Your topic" \
  --asset-first \
  --asset-video ./recording.mp4
```

### Key Benefits

- **Full duration preserved** — Your 10-min walkthrough stays 10 mins (not truncated)
- **Contextual narration** — OCR reads your terminal/code/browser → generates matching narration
- **Precise timing** — Explicit timestamps keep everything in sync
- **Same pipeline** — Reuses all existing agents (TTS, visuals, assembly, etc.)

---

## VoiceoverAgent Capabilities (v0.2.0+)

The `VoiceoverAgent` now supports **Supertonic TTS** — a lightning-fast, on-device, multilingual TTS system running via ONNX Runtime with zero VRAM usage:

| Provider | Model | Hardware | Quality |
|----------|-------|----------|---------|
| **Supertonic (M4)** | 99M params, ONNX Runtime | **CPU only** | 44.1kHz studio quality |
| OmniVoice | Various | GPU | High |
| Edge TTS | Microsoft Neural | CPU | Good |

### Configuration

```json
{
  "voice_provider": "supertonic_http",
  "supertonic": {
    "base_url": "http://127.0.0.1:7788",
    "voice_name": "M4",
    "total_steps": 8,
    "speed": 1.05
  }
}
```

### External Dependency (install once)

```bash
pip install 'supertonic[serve]'
supertonic serve --host 127.0.0.1 --port 7788
```

### Usage

```python
from youtube_factory.agents.voice import VoiceoverAgent

agent = VoiceoverAgent(config)
result = agent.run({
    "script_output": {"script_file": "...", "script_text": "..."},
    "run_dir": "/tmp/run"
})
```

The agent auto-detects `voice_provider: "supertonic_http"` and routes to the local Supertonic server (OpenAI-compatible `/v1/audio/speech` endpoint).

---

## Development

```bash
# Clone
git clone https://github.com/bgill55/youtube-factory-pipeline.git
cd youtube-factory-pipeline

# Install in editable mode with dev deps
pip install -e ".[dev]"

# Run tests
pytest tests/

# Lint
ruff check src/
mypy src/youtube_factory/
```

---

## Related Projects

- **FreeLLMAPI** — The unified LLM router this pipeline depends on
- **OmniVoice-Studio** — Local TTS service
- **YouTube Factory** — Full production deployment (Flask dashboard, scheduler, bootstrap scripts)

---

## License

MIT © 2026 bgill55

---

## Disclaimer

> **This is the orchestration library only.**  
> You must run the required external services yourself:
> - FreeLLMAPI (port 3001)
> - OmniVoice-Studio (port 3900) 
> - Stable Diffusion WebUI --api (port 7860)
> - FFmpeg (system binary)
> 
> See the main [YouTube Factory](https://github.com/bgill55/YouTube_Factory) repo for Docker Compose, bootstrap scripts, and the Flask dashboard.
