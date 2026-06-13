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
| `ResearchAgent` | GitHub/HF/arXiv/web analysis | Structured JSON briefs from any URL |
| `IdeaGeneratorAgent` | Video concept + SWOT | 3 concepts → 1 selected, niche diversity enforced |
| `ScriptwriterAgent` | 3-act retention scripts | Hook → Breakdown → Deep Dive + CTA |
| `VoiceoverAgent` | TTS via OmniVoice/Edge | Multi-speaker, word-level timestamps |
| `VisualAssetAgent` | B-roll + AI images + thumbnails | Pexels / SD / Fal / Pollinations / local |
| `AudioGenAgent` | BGM via Stable Audio 3 | Ducking, volume automation |
| `VideoAssemblerAgent` | FFmpeg assembly | Subtitles, ducking, 4K output |
| `ShortGenerator` | 9:16 vertical clips | Auto-scene selection ≤58s |
| `UploaderAgent` | YouTube Data API v3 | Playlists, thumbnails, Shorts |
| `GuideGeneratorAgent` | HTML resource pages | SEO-optimized, deployable to GitHub Pages |
| `CommunityAgent` | YouTube Community posts | Auto-generated from video outputs |

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

```
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

**All LLM calls route through FreeLLMAPI** → automatic provider failover, penalty tracking, system prompt injection.

---

## Provider Abstraction

| Capability | Providers | Fallback Chain |
|------------|-----------|----------------|
| **LLM** | Gemini, Cerebras, Groq, Z.ai, LM Studio | FreeLLMAPI router |
| **TTS** | OmniVoice, Edge TTS | VoiceoverAgent |
| **Images** | SD WebUI, Pollinations, Gemini, Fal | VisualAssetAgent |
| **Video** | Fal (Hunyuan), Kling, Veo, Pexels | VideoProviderManager |
| **BGM** | Stable Audio 3 | AudioGenAgent |
| **Stock** | Pexels | VisualAssetAgent |

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
