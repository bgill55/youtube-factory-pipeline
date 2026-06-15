#!/usr/bin/env python3
"""Generate a categorized, visually engaging README for the Weight and See Guides repo.

Automatically categorizes guides based on title/content keywords — no manual slug lists needed.
"""

import os
import re
import sys
from datetime import datetime

# Allow running from pipeline (for auto-deploy) or from repo root
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# Check if we're in the repo (has .git) or in pipeline
if os.path.exists(os.path.join(THIS_DIR, ".git")):
    REPO_DIR = THIS_DIR
elif os.path.exists(os.path.join(THIS_DIR, "..", "workspace", "runs", "_guides_repo", ".git")):
    REPO_DIR = os.path.join(THIS_DIR, "..", "workspace", "runs", "_guides_repo")
else:
    REPO_DIR = THIS_DIR  # fallback

GUIDES_DIR = os.path.join(REPO_DIR, "guides")

# Category definitions with keyword matching (same logic as playlist_manager.py)
# Order matters - first match wins
CATEGORY_KEYWORDS = {
    "Benchmarks & Comparisons": {
        "desc": "Head-to-head model showdowns and real-world performance tests",
        "keywords": ["vs ", " versus ", "benchmark", "showdown", "comparison", "speed test", "faceoff", "head-to-head", " vs."],
    },
    "Model Deep Dives": {
        "desc": "In-depth analysis of cutting-edge AI models and architectures",
        "keywords": ["deep dive", "paper", "breakdown", "analysis", "architecture", "deep-dive", "technical deep", "model card"],
    },
    "Local AI & Self-Hosting": {
        "desc": "Run powerful AI models on your own hardware — no cloud required",
        "keywords": ["local", "offline", "self-host", "raspberry pi", "laptop", "selfhost", "homelab", "edge ai", "run on your", "air-gap"],
    },
    "AI Security": {
        "desc": "Threats, vulnerabilities, and defenses in the AI era",
        "keywords": ["security", "breach", "hack", "attack", "vulnerability", "firewall", "poison", "exploit", "adversarial", "forge"],
    },
    "Developer Tools & Agents": {
        "desc": "AI-powered coding assistants, agents, and developer workflows",
        "keywords": ["agent", "coding", "cursor", "ide", "developer", "copilot", "vscode", "build a", "hands-on tutorial", "tutorial"],
    },
    "Image & Vision": {
        "desc": "Image generation, computer vision, and visual AI",
        "keywords": ["image", "vision", "diffusion", "stable diffusion", "cad", "midjourney", "flux", "dalle", "text-to-cad", "vision"],
    },
    "No-Code & Automation": {
        "desc": "Build AI workflows without writing code",
        "keywords": ["no-code", "nocode", "automation", "workflow", "flowise", "n8n", "zapier", "make.com", "no code"],
    },
    "GPU & Hardware": {
        "desc": "GPU benchmarks, hardware analysis, and acceleration",
        "keywords": ["gpu", "nvidia", "rtx", "4090", "3090", "vram", "cuda", "hardware benchmark", "acceleration"],
    },
    "Audio & Voice": {
        "desc": "TTS, voice synthesis, and audio AI",
        "keywords": ["tts", "voice", "audio", "speech", "synthesis", "kokoro", "whisper", "elevenlabs", "text-to-speech"],
    },
}

# Manual title overrides for slugs that need cleanup
TITLE_OVERRIDES = {
    "autovision-vs-stable-diffusion-31-edge-image-generation-showdown": "AutoVision vs Stable Diffusion 3.1 — Edge Image Generation Showdown",
    "build-a-gemini-optimized-app-on-apple-silicon-hands-on-tutorial": "Build a Gemini-Optimized App on Apple Silicon",
    "build-a-nocode-claude-fable-5-agent-in-5-minutes-no-coding-required": "Build a No-Code Claude FABLE 5 Agent in 5 Minutes",
    "cad-gpt-20-generating-production-ready-step-files-in-seconds": "CAD-GPT 2.0 — Generating Production-Ready STEP Files in Seconds",
    "claude-fable-5-desktop-test-realworld-speed-on-a-hyperv-vm": "Claude FABLE 5 Desktop — Real-World Speed on a Hyper-V VM",
    "deep-dive-into-adahmpleng-50m-5ep-1e-4-64b-efficient-smallscale-english-model": "Deep Dive into AdaHmpLEng — Efficient Small-Scale English Model",
    "diffusiongemma-26b-a4bit-the-new-fast-local-image-generator-for-creators": "DiffusionGemma 26B A4Bit — Fast Local Image Generator for Creators",
    "diffusiongemma-4x-faster-text-generation-how-the-new-model-breaks-speed-limits": "DiffusionGemma 4x Faster Text Generation — Speed Limits Broken",
    "gemma-4-vs-gpt55-deepseekv4-realworld-12b-benchmark-showdown": "Gemma 4 vs GPT-5.5 vs DeepSeek V4 — Real-World 12B Benchmark Showdown",
    "gemma412bit-vs-llama4-the-lightweight-coding-ai-showdown": "Gemma 4 12-Bit vs Llama 4 — The Lightweight Coding AI Showdown",
    "gpt4o-vs-claude-35-vs-gemini-20-realworld-office-task-benchmark-june-2026": "GPT-4o vs Claude 3.5 vs Gemini 2.0 — Real-World Office Task Benchmark",
    "hustlegemini-vs-cursor-6-the-agentic-coding-showdown": "HustleGemini vs Cursor 6 — The Agentic Coding Showdown",
    "inside-the-microsoft-ai-tool-breach-timeline-exploits-and-patch-rollout": "Inside the Microsoft AI Tool Breach — Timeline, Exploits & Patches",
    "is-outlines-30-the-ultimate-ai-firewall": "Is Outlines 3.0 the Ultimate AI Firewall?",
    "llama-4-cad-generate-engineering-grade-parts-offline": "Llama 4 CAD — Generate Engineering-Grade Parts Offline",
    "llama-4-turbo-local-the-48gb-vram-reality-check": "Llama 4 Turbo Local — The 48GB VRAM Reality Check",
    "metavision-20-api-deep-dive-is-the-new-multimodal-model-worth-the-hype": "MetaVision 2.0 API Deep Dive — Is It Worth the Hype?",
    "nocode-ai-orchestrators-faceoff-flowise-20-vs-n8n-ai-30-vs-autogptstudio": "No-Code AI Orchestrators Face-Off — Flowise vs n8n vs AutoGPT Studio",
    "ollama-07-offline-13b-llm-on-an-8-gb-laptop-does-it-really-work": "Ollama 0.7 — Offline 13B LLM on an 8GB Laptop",
    "qwen-3-72b-vs-llama-4-scout-realworld-macos-container-machine-benchmark-on-m3-ma": "Qwen 3 72B vs Llama 4 Scout — macOS Container Benchmark on M3",
    "qwen-3-72b-vs-llama-4-scout-realworld-speed-test-on-a-500-gpu": r"Qwen 3 72B vs Llama 4 Scout — Speed Test on a $500 GPU",
    "run-claude-35-offline-for-free-opencode-full-setup-on-a-500-pc": r"Run Claude 3.5 Offline for Free — Full Setup on a $500 PC",
    "run-gpt55-on-a-raspberry-pi-zero-the-ultimate-lowcost-local-ai-hack": "Run GPT-5.5 on a Raspberry Pi Zero — The Ultimate Low-Cost AI Hack",
    "running-ideogram-4-fp8-locally-fp8-quantization-benchmarks-and-cost-analysis": "Running Ideogram 4 FP8 Locally — Quantization Benchmarks & Cost",
    "running-llms-offline-a-stepbystep-guide": "Running LLMs Offline — A Step-by-Step Guide",
    "text-to-cad-just-got-real-the-caddy-model-breakdown": "Text-to-CAD Just Got Real — The CADDY Model Breakdown",
    "the-microsoft-ai-forge-hack-how-they-stole-your-api-keys": "The Microsoft AI Forge Hack — How They Stole Your API Keys",
    "the-omnivision-7-paper-why-ai-finally-understands-motion": "The OmniVision 7 Paper — Why AI Finally Understands Motion",
    "the-poise-attack-i-poisoned-an-ai-agent-in-real-time": "The POISE Attack — I Poisoned an AI Agent in Real Time",
    "unchaining-ai-is-qwen-36-aggressive-the-ultimate-local-powerhouse": "Unchaining AI — Is Qwen 3.6 Aggressive the Ultimate Local Powerhouse?",
    "unicad-7b-vs-cloud-giants-can-local-ai-engineer-real-parts": "UniCAD 7B vs Cloud Giants — Can Local AI Engineer Real Parts?",
    "unlocking-ai-voice-synthesis-a-deep-dive-into-sundaycoiltext-to-speech-converter": "Unlocking AI Voice Synthesis — A Deep Dive into sundaycoil/text-to-speech",
    "visionaryai-2026-review-8k-images-on-a-laptop-gpu": "VisionaryAI 2026 Review — 8K Images on a Laptop GPU",
    "whispersmallhi-the-tiny-transcriber-that-beats-cloud-apis": "WhisperSmall-Hi — The Tiny Transcriber That Beats Cloud APIs",
}

# Explicit category overrides for slugs where content-based categorization doesn't match intent
CATEGORY_OVERRIDES = {
    "build-a-gemini-optimized-app-on-apple-silicon-hands-on-tutorial": "Developer Tools & Agents",
    "build-a-nocode-claude-fable-5-agent-in-5-minutes-no-coding-required": "No-Code & Automation",
    "claude-fable-5-desktop-test-realworld-speed-on-a-hyperv-vm": "Developer Tools & Agents",
    "run-claude-35-offline-for-free-opencode-full-setup-on-a-500-pc": "Local AI & Self-Hosting",
    "running-llms-offline-a-stepbystep-guide": "Local AI & Self-Hosting",
    "run-gpt55-on-a-raspberry-pi-zero-the-ultimate-lowcost-local-ai-hack": "Local AI & Self-Hosting",
    "nocode-ai-orchestrators-faceoff-flowise-20-vs-n8n-ai-30-vs-autogptstudio": "No-Code & Automation",
    "cad-gpt-20-generating-production-ready-step-files-in-seconds": "Image & Vision",
    "llama-4-cad-generate-engineering-grade-parts-offline": "Image & Vision",
    "text-to-cad-just-got-real-the-caddy-model-breakdown": "Image & Vision",
    "diffusiongemma-26b-a4bit-the-new-fast-local-image-generator-for-creators": "Image & Vision",
    "autovision-vs-stable-diffusion-31-edge-image-generation-showdown": "Image & Vision",
}


def slug_to_title(slug):
    """Convert a URL slug to a readable title."""
    if slug in TITLE_OVERRIDES:
        return TITLE_OVERRIDES[slug]
    return slug.replace("-", " ").title()


def get_guide_title_and_text(slug):
    """Extract title and text content from a guide's index.html for categorization."""
    html_path = os.path.join(GUIDES_DIR, slug, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract from <title> tag
        title_match = re.search(r"<title>([^<]+)</title>", content)
        title = title_match.group(1).split("|")[0].strip() if title_match else slug
        # Get first 2000 chars of body text for keyword matching
        body_match = re.search(r"<body[^>]*>(.*?)</body>", content, re.DOTALL)
        body_text = body_match.group(1) if body_match else content[:2000]
        # Strip HTML tags
        body_text = re.sub(r"<[^>]+>", " ", body_text)
        return title, body_text[:2000].lower()
    return slug, ""


def categorize_slug(slug):
    """Auto-categorize a guide slug based on title and content keywords."""
    # Check explicit overrides first
    if slug in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[slug]
    
    title, body_text = get_guide_title_and_text(slug)
    text = f"{title} {body_text}".lower()
    
    for cat_name, cat_data in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in cat_data["keywords"]):
            return cat_name
    
    return "More Guides"  # Default fallback


def get_guide_datetime(slug):
    """Extract date and time from guide HTML for sorting and display."""
    html_path = os.path.join(GUIDES_DIR, slug, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Look for YYYY-MM-DD HH:MM
        match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', content)
        if match:
            return match.group(1)
        # Fallback to YYYY-MM-DD
        match = re.search(r'(\d{4}-\d{2}-\d{2})', content)
        if match:
            return match.group(1)
    return "0000-00-00 00:00"


def get_guide_date(slug):
    """Try to extract date from guide HTML (for display)."""
    dt = get_guide_datetime(slug)
    if dt != "0000-00-00 00:00":
        return dt.split()[0]
    return None


def has_thumbnail(slug):
    """Check if guide has a thumbnail."""
    return os.path.exists(os.path.join(GUIDES_DIR, slug, "thumbnail.jpg"))


def get_all_categorized_guides():
    """Scan guides directory and return categorized dict."""
    if not os.path.exists(GUIDES_DIR):
        return {}
    
    all_guides = [d.name for d in os.scandir(GUIDES_DIR) if d.is_dir() and d.name != "index.html"]
    
    # Categorize each guide
    categorized = {cat: {"desc": data["desc"], "slugs": []} for cat, data in CATEGORY_KEYWORDS.items()}
    categorized["More Guides"] = {"desc": "Recently published and uncategorized guides", "slugs": []}
    
    for slug in all_guides:
        cat = categorize_slug(slug)
        categorized[cat]["slugs"].append(slug)
    
    # Sort the guides in each category by date/time (newest first)
    for cat_data in categorized.values():
        cat_data["slugs"].sort(key=lambda s: get_guide_datetime(s), reverse=True)
        
    # Remove empty categories
    return {k: v for k, v in categorized.items() if v["slugs"]}


def generate_readme():
    CATEGORIES = get_all_categorized_guides()
    
    lines = []

    # Hero section
    lines.append('<div align="center">')
    lines.append('')
    lines.append('<img src="assets/hero-banner.png" width="100%" alt="Weight and See Guides Wiki">')
    lines.append('')
    lines.append('</div>')
    lines.append('')
    lines.append('---')
    lines.append('')

    # Stats bar
    total_guides = sum(len(cat["slugs"]) for cat in CATEGORIES.values())
    categories_count = len(CATEGORIES)
    lines.append('<div align="center">')
    lines.append('')
    month_year = datetime.now().strftime("%B %Y").upper()
    lines.append(f'![Guides](https://img.shields.io/badge/{total_guides}_GUIDES-blue?style=for-the-badge&logo=booktype&logoColor=white)')
    lines.append(f'![Categories](https://img.shields.io/badge/{categories_count}_CATEGORIES-green?style=for-the-badge&logo=folder-open&logoColor=white)')
    lines.append(f'![Updated](https://img.shields.io/badge/UPDATED_{month_year.replace(" ", "_")}-orange?style=for-the-badge&logo=simpleicons&logoColor=white)')
    lines.append('')
    lines.append('</div>')
    lines.append('')
    lines.append('---')
    lines.append('')

    # Latest guides (last 3 by extracted date-time)
    all_slugs_with_time = []
    for cat_data in CATEGORIES.values():
        for slug in cat_data["slugs"]:
            dt = get_guide_datetime(slug)
            all_slugs_with_time.append((slug, dt))
    # Sort latest first (reverse=True)
    all_slugs_with_time.sort(key=lambda x: x[1], reverse=True)

    lines.append('## Latest Guides')
    lines.append('')
    lines.append('<table><tr>')
    for slug, _ in all_slugs_with_time[:3]:
        title = slug_to_title(slug)
        url = f"https://bgill55.github.io/-weightandsee-guides/guides/{slug}/"
        thumb_path = os.path.join(GUIDES_DIR, slug, "thumbnail.jpg")
        if os.path.exists(thumb_path):
            lines.append(f'<td align="center" width="33%">')
            lines.append(f'<a href="{url}">')
            lines.append(f'<img src="guides/{slug}/thumbnail.jpg" width="300" alt="{title}"><br>')
            lines.append(f'<b>{title}</b>')
            lines.append(f'</a>')
            lines.append(f'</td>')
    lines.append('</tr></table>')
    lines.append('')
    lines.append('---')
    lines.append('')

    # Quick nav
    lines.append('## Quick Navigation')
    lines.append('')
    lines.append('| Category | Count |')
    lines.append('|----------|-------|')
    for cat_name, cat_data in CATEGORIES.items():
        count = len(cat_data["slugs"])
        lines.append(f'| **{cat_name}** | ![{count}](https://img.shields.io/badge/{count}-blue?style=flat-square) |')
    lines.append('')
    lines.append('---')
    lines.append('')

    # Each category
    for cat_name, cat_data in CATEGORIES.items():
        lines.append(f'## {cat_name}')
        lines.append('')
        lines.append(f'*{cat_data["desc"]}*')
        lines.append('')

        for slug in cat_data["slugs"]:
            title = slug_to_title(slug)
            url = f"https://bgill55.github.io/-weightandsee-guides/guides/{slug}/"
            date = get_guide_date(slug)
            date_str = f" — {date}" if date else ""
            lines.append(f'- **[{title}]({url})**{date_str}')

        lines.append('')
        lines.append('---')
        lines.append('')

    # Footer
    lines.append('<div align="center">')
    lines.append('')
    lines.append('### 📺 Watch the Videos')
    lines.append('')
    lines.append('Each guide corresponds to a video on the [Weight and See](https://youtube.com/@WeightnSee) YouTube channel.')
    lines.append('Watch the video for visual walkthroughs, then use the guide for code snippets and step-by-step instructions.')
    lines.append('')
    lines.append('[![YouTube](https://img.shields.io/badge/YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://youtube.com/@WeightnSee)')
    lines.append('')
    lines.append('</div>')

    return "\n".join(lines)


if __name__ == "__main__":
    readme = generate_readme()
    readme_path = os.path.join(REPO_DIR, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)
    
    CATEGORIES = get_all_categorized_guides()
    total = sum(len(c['slugs']) for c in CATEGORIES.values())
    print(f"README.md generated with {total} guides in {len(CATEGORIES)} categories (auto-categorized)")
