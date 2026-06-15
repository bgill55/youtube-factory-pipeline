import os
import json
import re
import subprocess
import asyncio
import threading
import requests
import edge_tts


class VoiceoverAgent:
    def __init__(self, config):
        self.config = config

    def get_audio_duration(self, file_path):
        """Uses ffprobe to get the duration of an audio file in seconds."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            return float(result.stdout.strip())
        except Exception as e:
            return 2.0

    def generate_tts_elevenlabs(self, text, output_file):
        el_config = self.config.get("elevenlabs", {})
        api_key = el_config.get("api_key")
        voice_id = el_config.get("voice_id", "21m00Tcm4TlvDq8ikWAM")

        if not api_key or api_key == "YOUR_ELEVENLABS_API_KEY":
            raise ValueError("ElevenLabs API Key is not configured.")

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            raise Exception(f"ElevenLabs TTS failed with status {response.status_code}: {response.text}")

        with open(output_file, "wb") as f:
            f.write(response.content)

    def generate_tts_edgetts(self, text, output_file, srt_output_file=None):
        fallback_config = self.config.get("voice_fallback", {})
        voice = fallback_config.get("tts_voice", "en-US-GuyNeural")
        tts_timeout = self.config.get("voice_fallback", {}).get("tts_timeout", 60)

        async def _speak():
            communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
            sub_maker = edge_tts.SubMaker()
            with open(output_file, "wb") as audio_f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        sub_maker.feed(chunk)
            return sub_maker

        import concurrent.futures
        result = [None]
        exc = [None]

        def _target():
            try:
                result[0] = asyncio.run(_speak())
            except Exception as e:
                exc[0] = e

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=tts_timeout)

        if thread.is_alive():
            raise TimeoutError(f"edge-tts timed out after {tts_timeout}s for text: {text[:50]}...")

        if exc[0]:
            raise exc[0]

        sub_maker = result[0]

        if srt_output_file and sub_maker:
            try:
                subs = sub_maker.get_srt()
                if subs and subs.strip():
                    with open(srt_output_file, "w", encoding="utf-8") as f:
                        f.write(subs)
                    return True
            except Exception as e:
                print(f"[Voice Agent] SubMaker SRT generation failed: {e}")
        return False

    def generate_tts_omnivoice(self, text, output_file, srt_output_file=None):
        ov_config = self.config.get("omnivoice", {})
        base_url = ov_config.get("base_url", "http://127.0.0.1:3900/v1")
        voice_id = ov_config.get("voice_id", "default")
        model_name = ov_config.get("model_name", "omnivoice")
        language = ov_config.get("language")
        speed = ov_config.get("speed", 1.0)

        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key="local-omnivoice")

        opts = {
            "model": model_name,
            "voice": voice_id,
            "input": text,
            "response_format": "mp3",
            "speed": speed
        }
        if language:
            opts["extra_body"] = {"language": language}

        response = client.audio.speech.create(**opts)
        with open(output_file, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)

        if srt_output_file:
            try:
                import requests
                url = f"{base_url}/audio/transcriptions"
                with open(output_file, "rb") as audio_file:
                    files = {"file": (os.path.basename(output_file), audio_file, "audio/mpeg")}
                    data = {"model": "whisper-1", "response_format": "verbose_json"}
                    trans_response = requests.post(url, files=files, data=data)

                if trans_response.status_code == 200:
                    result = trans_response.json()
                    words = []
                    for seg in result.get("segments", []):
                        for w in seg.get("words", []):
                            w_text = w.get("word")
                            w_start = w.get("start")
                            w_end = w.get("end")
                            if w_text is not None and w_start is not None and w_end is not None:
                                words.append((w_text.strip(), w_start, w_end))

                    if words:
                        srt_lines = []
                        for idx, (w_text, start_sec, end_sec) in enumerate(words, 1):
                            start_str = self._format_seconds_to_srt(start_sec)
                            end_str = self._format_seconds_to_srt(end_sec)
                            srt_lines.append(f"{idx}\n{start_str} --> {end_sec}\n{w_text}\n")

                        with open(srt_output_file, "w", encoding="utf-8") as srt_f:
                            srt_f.write("\n".join(srt_lines))
                        return True
            except Exception as e:
                print(f"[Voice Agent] OmniVoice SRT alignment extraction failed: {e}")
        return False

    def _format_seconds_to_srt(self, seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def generate_tts_gemini(self, text, output_file):
        gemini_config = self.config.get("gemini", {})
        api_key = gemini_config.get("api_key")
        model_name = gemini_config.get("voice_model", "gemini-3.1-flash-tts-preview")
        voice_name = gemini_config.get("voice_name", "Puck")

        if not api_key or api_key == "YOUR_GEMINI_API_KEY":
            raise ValueError("Gemini API Key is not configured.")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model=model_name,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                ),
            ),
        )
        audio_data = response.candidates[0].content.parts[0].inline_data.data

        temp_wav = output_file + ".tmp.wav"
        with open(temp_wav, "wb") as f:
            f.write(audio_data)

        cmd = [
            "ffmpeg", "-y",
            "-i", temp_wav,
            "-codec:a", "libmp3lame",
            "-b:a", "192k",
            output_file
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)

        if os.path.exists(temp_wav):
            os.remove(temp_wav)

    def generate_tts_supertonic(self, text, output_file, srt_output_file=None):
        st_config = self.config.get("supertonic", {})
        base_url = st_config.get("base_url", "http://127.0.0.1:7788")
        voice_name = st_config.get("voice_name", "M4")
        total_steps = st_config.get("total_steps", 8)
        speed = st_config.get("speed", 1.05)

        url = f"{base_url}/v1/audio/speech"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": "supertonic-3",
            "input": text,
            "voice": voice_name,
            "response_format": "wav",
            "extra_body": {
                "total_steps": total_steps,
                "speed": speed
            }
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            raise Exception(f"Supertonic TTS failed with status {response.status_code}: {response.text}")

        with open(output_file, "wb") as f:
            f.write(response.content)

        if srt_output_file:
            try:
                trans_url = f"{base_url}/v1/audio/transcriptions"
                with open(output_file, "rb") as audio_file:
                    files = {"file": (os.path.basename(output_file), audio_file, "audio/wav")}
                    data = {"model": "whisper-1", "response_format": "verbose_json", "timestamp_granularities": "word"}
                    trans_response = requests.post(trans_url, files=files, data=data)

                if trans_response.status_code == 200:
                    result = trans_response.json()
                    words = []
                    for seg in result.get("segments", []):
                        for w in seg.get("words", []):
                            w_text = w.get("word")
                            w_start = w.get("start")
                            w_end = w.get("end")
                            if w_text is not None and w_start is not None and w_end is not None:
                                words.append((w_text.strip(), w_start, w_end))

                    if words:
                        srt_lines = []
                        for idx, (w_text, start_sec, end_sec) in enumerate(words, 1):
                            start_str = self._format_seconds_to_srt(start_sec)
                            end_str = self._format_seconds_to_srt(end_sec)
                            srt_lines.append(f"{idx}\n{start_str} --> {end_sec}\n{w_text}\n")

                        with open(srt_output_file, "w", encoding="utf-8") as srt_f:
                            srt_f.write("\n".join(srt_lines))
                        return True
            except Exception as e:
                print(f"[Voice Agent] Supertonic SRT alignment extraction failed: {e}, falling back to edge-tts")
                return self.generate_tts_edgetts(text, output_file, srt_output_file=srt_output_file)
        return False

    def run(self, inputs):
        script_output = inputs.get("script_output")
        run_dir = inputs.get("run_dir")

        script_file = script_output.get("script_file")
        if not script_file or not os.path.exists(script_file):
            raise FileNotFoundError(f"Script file not found: {script_file}")

        with open(script_file, "r", encoding="utf-8") as f:
            script_content = f.read()

        # Parse script into scenes
        lines = script_content.splitlines()
        scenes = []
        current_visual = "Abstract introductory concept visual"
        current_spoken = []

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # Ignore markdown dividers/horizontal rules (like ***, ---, ___ or just spaces)
            if re.match(r'^[*\\-_ ]+$', line):
                continue

            # Check for visual cue - handle both [Visual: ...] and [Visual]: ...
            visual_match = re.match(r"^\[Visual\]?:\s*(.*)", line, re.IGNORECASE)
            if not visual_match:
                visual_match = re.match(r"^\[Visual:\s*(.*)\]", line, re.IGNORECASE)
            if visual_match:
                if current_spoken:
                    scenes.append({
                        "visual_description": current_visual,
                        "spoken_text": " ".join(current_spoken)
                    })
                    current_spoken = []
                current_visual = visual_match.group(1).strip().rstrip("]")
                continue

            # Check for narrator text
            narrator_match = re.match(r"^\[Narrator\]:\s*(.*)", line, re.IGNORECASE)
            if narrator_match:
                current_spoken.append(narrator_match.group(1).strip())
            elif line.startswith("[") and "]" in line:
                pass
            elif not line.startswith("#") and not line.startswith("---"):
                if current_spoken:
                    current_spoken.append(line)

        # Append last scene if any
        if current_spoken:
            scenes.append({
                "visual_description": current_visual,
                "spoken_text": " ".join(current_spoken)
            })

        if not scenes:
            raise ValueError("No voiceover scenes or narrator lines could be parsed from the script.")

        audio_dir = os.path.join(run_dir, "audio")
        os.makedirs(audio_dir, exist_ok=True)

        processed_scenes = []
        total_duration = 0.0

        for idx, scene in enumerate(scenes):
            scene_audio_filename = f"scene_{idx}.mp3"
            scene_audio_path = os.path.join(audio_dir, scene_audio_filename)
            scene_srt_path = os.path.join(run_dir, f"scene_{idx}_words.srt")
            spoken_text = scene["spoken_text"]
            spoken_text = re.sub(r'\*+', '', spoken_text)
            spoken_text = re.sub(r'_+', '', spoken_text)
            spoken_text = re.sub(r'\s+', ' ', spoken_text).strip()

            voice_provider = self.config.get("voice_provider", "gemini")
            provider = "edge-tts"
            has_word_srt = False

            if voice_provider == "gemini":
                try:
                    self.generate_tts_gemini(spoken_text, scene_audio_path)
                    provider = "gemini"
                except Exception as e:
                    print(f"[Voice Agent] Gemini TTS failed for scene {idx}, falling back to Edge TTS. Error: {e}")
                    has_word_srt = self.generate_tts_edgetts(spoken_text, scene_audio_path, srt_output_file=scene_srt_path)
                    provider = "edge-tts"
            elif voice_provider == "elevenlabs":
                try:
                    self.generate_tts_elevenlabs(spoken_text, scene_audio_path)
                    provider = "elevenlabs"
                except Exception as e:
                    print(f"[Voice Agent] ElevenLabs TTS failed for scene {idx}, falling back to Edge TTS. Error: {e}")
                    has_word_srt = self.generate_tts_edgetts(spoken_text, scene_audio_path, srt_output_file=scene_srt_path)
                    provider = "edge-tts"
            elif voice_provider == "omnivoice":
                try:
                    has_word_srt = self.generate_tts_omnivoice(spoken_text, scene_audio_path, srt_output_file=scene_srt_path)
                    provider = "omnivoice"
                except Exception as e:
                    print(f"[Voice Agent] OmniVoice TTS failed for scene {idx}, falling back to Edge TTS. Error: {e}")
                    has_word_srt = self.generate_tts_edgetts(spoken_text, scene_audio_path, srt_output_file=scene_srt_path)
                    provider = "edge-tts"
            elif voice_provider == "supertonic_http":
                try:
                    temp_wav = scene_audio_path.replace(".mp3", ".wav")
                    has_word_srt = self.generate_tts_supertonic(spoken_text, temp_wav, srt_output_file=scene_srt_path)
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", temp_wav,
                        "-codec:a", "libmp3lame",
                        "-b:a", "192k",
                        scene_audio_path
                    ]
                    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
                    if os.path.exists(temp_wav):
                        os.remove(temp_wav)
                    provider = "supertonic"
                except Exception as e:
                    print(f"[Voice Agent] Supertonic TTS failed for scene {idx}, falling back to Edge TTS. Error: {e}")
                    has_word_srt = self.generate_tts_edgetts(spoken_text, scene_audio_path, srt_output_file=scene_srt_path)
                    provider = "edge-ttps"
            else:
                has_word_srt = self.generate_tts_edgetts(spoken_text, scene_audio_path, srt_output_file=scene_srt_path)

            duration = self.get_audio_duration(scene_audio_path)
            total_duration += duration

            processed_scenes.append({
                "scene_index": idx,
                "visual_description": scene["visual_description"],
                "spoken_text": spoken_text,
                "audio_file": scene_audio_path,
                "duration": duration,
                "provider": provider,
                "word_srt_file": scene_srt_path if has_word_srt else None
            })

        # Combine all audio files
        concat_list_path = os.path.join(audio_dir, "concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for scene in processed_scenes:
                safe_path = scene["audio_file"].replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        master_audio_path = os.path.join(audio_dir, "audio_master.mp3")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            master_audio_path
        ]

        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)

        voice_meta_path = os.path.join(run_dir, "voice_metadata.json")
        output_metadata = {
            "audio_file": master_audio_path,
            "duration": total_duration,
            "scenes": processed_scenes
        }
        with open(voice_meta_path, "w", encoding="utf-8") as f:
            json.dump(output_metadata, f, indent=2)

        return output_metadata