"""YouTube Factory Pipeline - Modular video production agents."""

__version__ = "0.1.0"

# Core orchestration
from youtube_factory.orchestrator import (
    PipelineOrchestrator,
    PipelineHaltError,
    STAGE_RESEARCH,
    STAGE_SCRAPE,
    STAGE_IDEA,
    STAGE_SCRIPT,
    STAGE_GUIDE,
    STAGE_VOICE,
    STAGE_VISUAL,
    STAGE_AUDIO_GEN,
    STAGE_ASSEMBLY,
    STAGE_SHORTS,
    STAGE_GUIDE_DEPLOY,
    STAGE_UPLOAD,
    STAGES,
)

# Config & LLM
from youtube_factory.config import load_config
from youtube_factory.llm import (
    query_llm,
    query_llm_async,
    query_llm_with_history,
    try_gemini,
    try_cerebras,
    try_groq,
    try_zai,
    try_lm_studio,
)
from youtube_factory.prompts import get_system_prompt, get_temperature
from youtube_factory.freellmapi import (
    FreeLLMAPIClient,
    FreeLLMAPIError,
    FreeLLMAPIResponse,
    query_freellmapi,
    query_freellmapi_sync,
)

# Agents
from youtube_factory.agents.idea import IdeaGeneratorAgent
from youtube_factory.agents.script import ScriptwriterAgent
from youtube_factory.agents.voice import VoiceoverAgent
from youtube_factory.agents.visuals import VisualAssetAgent
from youtube_factory.agents.audio import AudioGeneratorAgent
from youtube_factory.agents.assembly import VideoAssemblerAgent
from youtube_factory.shorts import ShortGenerator
from youtube_factory.agents.uploader import UploaderAgent
from youtube_factory.agents.guide import GuideGeneratorAgent
from youtube_factory.agents.community import CommunityPostAgent
from youtube_factory.agents.analytics import AnalyticsAgent
from youtube_factory.agents.research import ResearchAgent
from youtube_factory.agents.scraper import WebsiteScraper

# Utilities
from youtube_factory.scheduler import BackgroundScheduler
from youtube_factory.shortio import ShortioManager
from youtube_factory.guide_deploy import GuideDeployer
from youtube_factory.playlists import PlaylistManager
from youtube_factory.video_providers import VideoProviderManager
from youtube_factory.shorts import ShortGenerator

__all__ = [
    # Orchestration
    "PipelineOrchestrator",
    "PipelineHaltError",
    "STAGE_RESEARCH",
    "STAGE_SCRAPE",
    "STAGE_IDEA",
    "STAGE_SCRIPT",
    "STAGE_GUIDE",
    "STAGE_VOICE",
    "STAGE_VISUAL",
    "STAGE_AUDIO_GEN",
    "STAGE_ASSEMBLY",
    "STAGE_SHORTS",
    "STAGE_GUIDE_DEPLOY",
    "STAGE_UPLOAD",
    "STAGES",
    # Config & LLM
    "load_config",
    "query_llm",
    "query_llm_async",
    "query_llm_with_history",
    "try_gemini",
    "try_cerebras",
    "try_groq",
    "try_zai",
    "try_lm_studio",
    "get_system_prompt",
    "get_temperature",
    "FreeLLMAPIClient",
    "FreeLLMAPIError",
    "FreeLLMAPIResponse",
    "query_freellmapi",
    "query_freellmapi_sync",
    # Agents
    "IdeaGeneratorAgent",
    "ScriptwriterAgent",
    "VoiceoverAgent",
    "VisualAssetAgent",
    "AudioGeneratorAgent",
    "VideoAssemblerAgent",
    "ShortGenerator",
    "UploaderAgent",
    "GuideGeneratorAgent",
    "CommunityPostAgent",
    "AnalyticsAgent",
    "ResearchAgent",
    "WebsiteScraper",
    # Utilities
    "BackgroundScheduler",
    "ShortioManager",
    "GuideDeployer",
    "PlaylistManager",
    "VideoProviderManager",
]