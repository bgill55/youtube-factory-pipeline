import os
# Disable tqdm progress bars BEFORE any imports to avoid [Errno 22] Invalid argument in Flask context
# Must be set BEFORE torch/transformers/stable_audio_3 imports
os.environ["TQDM_DISABLE"] = "1"
import sys
import json
import subprocess
import numpy as np
import unicodedata
from pipeline.llm_utils import query_llm as _query_llm
from pipeline.prompts import get_system_prompt


def _safe_print(*args, **kwargs):
    """Print that never crashes on Windows cp1252 consoles or detached stdout."""
    try:
        text = " ".join(str(a) for a in args)
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"), **kwargs)
    except Exception:
        pass


def _normalize_text(text):
    """Replace fancy Unicode punctuation with plain ASCII equivalents.

    The LLM sometimes outputs non-breaking hyphens (\u2011), em-dashes (\u2014),
    curly quotes, etc. These crash Windows cp1252 print calls and confuse
    downstream music-generation prompts.
    """
    replacements = {
        "\u2011": "-",   # non-breaking hyphen  → regular hyphen
        "\u2012": "-",   # figure dash          → regular hyphen
        "\u2013": "-",   # en dash              → regular hyphen
        "\u2014": " - ", # em dash              → spaced hyphen
        "\u2018": "'",   # left single quote
        "\u2019": "'",   # right single quote
        "\u201c": '"',   # left double quote
        "\u201d": '"',   # right double quote
        "\u00a0": " ",   # non-breaking space   → regular space
        "\u202f": " ",   # narrow no-break space → regular space
    }
    for fancy, plain in replacements.items():
        text = text.replace(fancy, plain)
    # Final safety net: strip any remaining non-ASCII that slipped through
    return text.encode("ascii", errors="replace").decode("ascii")


class AudioGeneratorAgent:
    def __init__(self, config):
        self.config = config

    def query_llm_for_audio_prompt(self, selected_topic, keywords):
        """Converts video topic and keywords into a descriptive prompt for music generation."""
        system_prompt = get_system_prompt("audio_prompt", selected_topic=selected_topic, keywords=", ".join(keywords))
        user_prompt = "Generate the music prompt now."

        default_prompt = (
            f"Ambient electronic tech music, soft synthesizers, documentary background theme, "
            f"loopable, {selected_topic}, 100 bpm, clean mix"
        )

        try:
            prompt = _query_llm(self.config, system_prompt, user_prompt, task="audio_prompt")
            prompt = prompt.strip().replace('"', '').replace("'", "")
            if prompt:
                return prompt
        except Exception as e:
            _safe_print(f"[Audio Gen Agent] LLM prompt generation failed: {e}")

        return default_prompt

    def run(self, inputs):
        run_dir = inputs.get("run_dir")
        audio_duration = inputs.get("audio_duration", 30.0)
        idea_output = inputs.get("idea_output", {})

        bg_music = self.config.get("bg_music", "none")

        if bg_music == "none":
            print("[Audio Gen Agent] Background music is set to 'none'. Skipping.")
            return {
                "status": "SKIPPED",
                "bg_music_file": None,
                "prompt": ""
            }

        workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # If a preset file is selected (not stable_audio_3)
        if bg_music != "stable_audio_3":
            preset_path = os.path.join(workspace_dir, "assets", "background_music", bg_music)
            if os.path.exists(preset_path):
                print(f"[Audio Gen Agent] Using preset background music: {preset_path}")
                return {
                    "status": "SUCCESS",
                    "bg_music_file": preset_path,
                    "prompt": "Preset track"
                }
            else:
                print(f"[Audio Gen Agent] WARNING: Preset BGM file {bg_music} not found at {preset_path}. Skipping.")
                return {
                    "status": "FAILED",
                    "bg_music_file": None,
                    "prompt": ""
                }


        # Generate with Stable Audio 3
        _safe_print("[Audio Gen Agent] Initializing Stable Audio 3 generation...")
        selected_topic = _normalize_text(idea_output.get("selected_topic", "Tech Video"))
        keywords = [_normalize_text(k) for k in idea_output.get("keywords", ["technology"])]

        # Generate prompt using LLM and normalize away any fancy Unicode
        prompt = _normalize_text(self.query_llm_for_audio_prompt(selected_topic, keywords))
        _safe_print(f"[Audio Gen Agent] Music prompt: '{prompt}'")

        # Cap music duration at Stable Audio 3 max limit (120 seconds)
        music_seconds = min(float(audio_duration) + 5.0, 120.0)

        output_wav_name = "generated_bgm.wav"
        output_wav_path = os.path.join(run_dir, output_wav_name)
        output_mp3_path = os.path.join(run_dir, "generated_bgm.mp3")

        # Add stable-audio-3 repo to sys.path so we can import stable_audio_3
        repo_path = os.path.join(workspace_dir, "stable-audio-3")
        if repo_path not in sys.path:
            sys.path.insert(0, repo_path)

        import io
        # Save the REAL original stderr (sys.__stderr__) so the finally block
        # always restores it, even if this code path runs more than once.
        _original_stderr = sys.__stderr__
        sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")

        try:
            import torch
            import soundfile as sf
            from stable_audio_3 import StableAudioModel

            # Determine device - try CUDA first, fall back to CPU
            if torch.cuda.is_available():
                device = "cuda"
                _safe_print("[Audio Gen Agent] CUDA available, using GPU.")
            else:
                device = "cpu"
                _safe_print("[Audio Gen Agent] No CUDA, using CPU (will be slow.).")

            _safe_print(f"[Audio Gen Agent] Loading stable-audio-3 small-music model on {device}...")
            model_half = (device == "cuda")
            model = StableAudioModel.from_pretrained(
                "small-music",
                device=device,
                model_half=model_half
            )
            _safe_print(f"[Audio Gen Agent] Model loaded. Generating {music_seconds:.1f}s of audio...")

            output = model.generate(
                prompt=prompt,
                duration=music_seconds,
                steps=25,
                cfg_scale=1.0,
                batch_size=1
            )

            audio_tensor = output[0].cpu()
            sample_rate = model.model.sample_rate
            _safe_print(f"[Audio Gen Agent] Audio generated! Shape: {audio_tensor.shape}, sample_rate: {sample_rate}")

            audio_np = audio_tensor.float().numpy().T
            sf.write(output_wav_path, audio_np, sample_rate)
            _safe_print(f"[Audio Gen Agent] WAV audio saved to {output_wav_path}")

            subprocess.run([
                "ffmpeg", "-y",
                "-i", output_wav_path,
                "-codec:a", "libmp3lame",
                "-b:a", "192k",
                output_mp3_path
            ], capture_output=True, check=True)

            if os.path.exists(output_wav_path):
                os.remove(output_wav_path)

            _safe_print(f"[Audio Gen Agent] Generated MP3 saved successfully to {output_mp3_path}")

            return {
                "status": "SUCCESS",
                "bg_music_file": output_mp3_path,
                "prompt": prompt
            }

        except Exception as e:
            sys.stderr = _original_stderr
            _safe_print(f"[Audio Gen Agent] ERROR in audio generation: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "status": "FAILED",
                "bg_music_file": None,
                "prompt": locals().get("prompt", ""),
                "error": str(e)
            }
        finally:
            sys.stderr = _original_stderr
