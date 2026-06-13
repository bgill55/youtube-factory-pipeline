import os
import json
import re
import subprocess
import time
import shutil
from PIL import Image, ImageDraw, ImageFont
from youtube_factory.llm import query_llm as _query_llm
from youtube_factory.prompts import get_system_prompt

class ShortGenerator:
    def __init__(self, config):
        self.config = config

    def format_srt_time(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _render_topic_overlay(self, text, video_width, video_height, output_dir):
        """Render topic text onto a transparent PNG using PIL (same quality as thumbnails)."""
        try:
            # Create transparent canvas matching video size
            img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Load Impact font (same as thumbnails)
            font_size = 72
            windir = os.environ.get("WINDIR", "C:\\Windows")
            font_candidates = [
                os.path.join(windir, "Fonts", "impact.ttf"),
                os.path.join(windir, "Fonts", "Impact.ttf"),
                os.path.join(windir, "Fonts", "ariblk.ttf"),
            ]
            font = None
            for path in font_candidates:
                if os.path.exists(path):
                    try:
                        font = ImageFont.truetype(path, font_size)
                        break
                    except Exception:
                        pass
            if not font:
                font = ImageFont.load_default()

            # Measure text
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            # Position: centered horizontally, 75% from top (bottom area, away from CC)
            x = (video_width - text_w) // 2
            y = int(video_height * 0.75)

            # Draw semi-transparent black background bar
            padding = 18
            bar_left = x - padding
            bar_top = y - padding
            bar_right = x + text_w + padding
            bar_bottom = y + text_h + padding
            draw.rectangle([bar_left, bar_top, bar_right, bar_bottom], fill=(0, 0, 0, 160))

            # Draw gold text (signature color)
            draw.text((x, y), text, fill=(255, 223, 0, 255), font=font)

            overlay_path = os.path.join(output_dir, "topic_overlay.png")
            img.save(overlay_path, "PNG")
            print(f"[Short Generator] Topic overlay rendered: '{text}'")
            return overlay_path
        except Exception as e:
            print(f"[Short Generator] Topic overlay render failed: {e}")
            return None

    def write_scene_srt_timed(self, word_srt_path, srt_out_path):
        """Parse and group the word-level SRT into clean multi-word subtitle segments (Shorts optimization)."""
        def parse_srt_time(srt_time_str):
            match = re.match(r"(\d+):(\d+):(\d+)[,\.](\d+)", srt_time_str)
            if not match:
                raise ValueError(f"Invalid SRT time format: {srt_time_str}")
            hours, minutes, seconds, milliseconds = map(int, match.groups())
            return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0

        def format_srt_time(seconds):
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds - int(seconds)) * 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        if not os.path.exists(word_srt_path):
            return

        with open(word_srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        pattern = r"(\d+)\n(\d{2}:\d{2}:\d{2}[,\.]\d{3}) --> (\d{2}:\d{2}:\d{2}[,\.]\d{3})\n(.*)"
        matches = re.findall(pattern, content)

        words = []
        for idx, start_str, end_str, word in matches:
            word = word.strip()
            if not word:
                continue
            try:
                start = parse_srt_time(start_str)
                end = parse_srt_time(end_str)
                words.append({"word": word, "start": start, "end": end})
            except Exception as e:
                print(f"[Short Generator] Error parsing block {idx}: {e}")

        if not words:
            shutil.copy(word_srt_path, srt_out_path)
            return

        # Shorts constraints: fewer words per line, faster pace!
        max_words = 3
        max_duration = 1.8
        max_gap = 0.25

        grouped_segments = []
        current_group = []

        for w in words:
            if not current_group:
                current_group.append(w)
                continue

            first_word = current_group[0]
            last_word = current_group[-1]

            duration = w["end"] - first_word["start"]
            gap = w["start"] - last_word["end"]

            if (len(current_group) >= max_words or 
                duration > max_duration or 
                gap > max_gap):
                grouped_segments.append(current_group)
                current_group = [w]
            else:
                current_group.append(w)

        if current_group:
            grouped_segments.append(current_group)

        with open(srt_out_path, "w", encoding="utf-8") as f:
            for idx, group in enumerate(grouped_segments):
                start_time = group[0]["start"]
                end_time = group[-1]["end"]

                words_in_group = [g["word"].upper() for g in group] # UPPERCASE subtitles look better on Shorts
                display_text = " ".join(words_in_group)

                f.write(f"{idx + 1}\n")
                f.write(f"{format_srt_time(start_time)} --> {format_srt_time(end_time)}\n")
                f.write(f"{display_text}\n\n")

    def write_scene_srt(self, text, duration, srt_path):
        """Fallback: estimate subtitle timing from words-per-second rate."""
        words = text.split()
        if not words:
            return

        words_per_second = len(words) / duration if duration > 0 else 2.5
        words_per_segment = 3 # Smaller segments for Shorts
        segments = []
        for i in range(0, len(words), words_per_segment):
            segments.append(words[i:i+words_per_segment])

        with open(srt_path, "w", encoding="utf-8") as f:
            elapsed_words = 0
            for idx, segment in enumerate(segments):
                start_time = elapsed_words / words_per_second
                end_time = (elapsed_words + len(segment)) / words_per_second

                end_time = min(end_time, duration)
                if start_time >= duration:
                    break

                start_str = self.format_srt_time(start_time)
                end_str = self.format_srt_time(end_time)

                display_text = " ".join(segment).upper()

                f.write(f"{idx + 1}\n")
                f.write(f"{start_str} --> {end_str}\n")
                f.write(f"{display_text}\n\n")

                elapsed_words += len(segment)

    def fallback_scene_range(self, scenes):
        """Greedily find the range of scenes that maximizes duration while staying under 58s."""
        best_range = (0, 0, 0.0)
        for start in range(len(scenes)):
            dur = 0.0
            end = start
            for i in range(start, len(scenes)):
                if dur + scenes[i]["duration"] <= 58.0:
                    dur += scenes[i]["duration"]
                    end = i
                else:
                    break
            if dur > best_range[2]:
                best_range = (start, end, dur)
        return best_range[0], best_range[1], best_range[2]

    def select_scenes_with_llm(self, scenes):
        """Asks the LLM to choose the best range of consecutive scenes under 58 seconds."""
        scenes_json = json.dumps([
            {"index": i, "duration": s["duration"], "spoken_text": s["spoken_text"]}
            for i, s in enumerate(scenes)
        ])
        system_prompt = get_system_prompt("shorts_selection", scenes_json=scenes_json)

        scene_list_str = "\n".join([
            f"Scene {i}: Duration: {s['duration']:.2f}s | Spoken: '{s['spoken_text']}'"
            for i, s in enumerate(scenes)
        ])
        user_prompt = f"Here is the list of scenes:\n\n{scene_list_str}\n\nSelect the best scene range JSON."

        try:
            res = _query_llm(self.config, system_prompt, user_prompt, task="shorts_selection", require_json=True)
            clean_text = res.strip()
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean_text = "\n".join(lines).strip()

            data = json.loads(clean_text)
            start_idx = int(data["start_scene_index"])
            end_idx = int(data["end_scene_index"])
            reason = data.get("reason", "Selected by AI Shorts Producer")

            # Validate range
            if 0 <= start_idx <= end_idx < len(scenes):
                total_dur = sum([scenes[i]["duration"] for i in range(start_idx, end_idx + 1)])
                if total_dur <= 58.0:
                    return start_idx, end_idx, reason

        except Exception as e:
            print(f"[Short Generator] LLM scene selection failed: {e}. Falling back to greedy range.")

        # Fallback
        s, e, d = self.fallback_scene_range(scenes)
        return s, e, "Selected programmatically to maximize duration under 55s."

    def generate(self, run_dir, orchestrator=None):
        """Assembles the short, returns the result dict."""
        print(f"[Short Generator] Initializing Short generation in: {run_dir}")
        state_path = os.path.join(run_dir, "run_state.json")
        if not os.path.exists(state_path):
            raise FileNotFoundError(f"Run state file not found: {state_path}")

        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)

        voice_meta_path = os.path.join(run_dir, "voice_metadata.json")
        visual_meta_path = os.path.join(run_dir, "visual_metadata.json")

        if not os.path.exists(voice_meta_path) or not os.path.exists(visual_meta_path):
            raise FileNotFoundError("Missing voice_metadata.json or visual_metadata.json in run dir.")

        with open(voice_meta_path, "r", encoding="utf-8") as f:
            voice_meta = json.load(f)
        with open(visual_meta_path, "r", encoding="utf-8") as f:
            visual_meta = json.load(f)

        scenes_voice = voice_meta.get("scenes", [])
        scenes_visual = visual_meta.get("visual_assets", [])

        if not scenes_voice or not scenes_visual:
            raise ValueError("No scenes found in metadata.")

        # Select indices
        start_idx, end_idx, reason = self.select_scenes_with_llm(scenes_voice)
        total_duration = sum([scenes_voice[i]["duration"] for i in range(start_idx, end_idx + 1)])
        # Safe encoding print to prevent Windows terminal character crashes
        safe_reason = reason.encode('ascii', errors='replace').decode('ascii')
        print(f"[Short Generator] Selected scenes {start_idx} to {end_idx} ({total_duration:.2f}s). Reason: {safe_reason}")

        # Setup temp short directory
        short_temp_dir = os.path.join(run_dir, "video_temp_short")
        if os.path.exists(short_temp_dir):
            shutil.rmtree(short_temp_dir)
        os.makedirs(short_temp_dir, exist_ok=True)

        video_settings = self.config.get("video_settings", {})
        fps = video_settings.get("fps", 30)

        # Target size for Shorts
        width = 1080
        height = 1920

        # Generate topic overlay PNG using PIL (clean text like thumbnails)
        topic_overlay_path = None
        idea_output = state.get("steps", {}).get("IDEA_GEN", {}).get("output", {})
        raw_topic = idea_output.get("selected_topic") or state.get("topic_seed", "")
        topic_label = raw_topic
        if raw_topic:
            try:
                system_prompt = (
                    "You are a video editor creating a short on-screen label for a YouTube Short intro.\n"
                    "Condense the video topic into a punchy 3-5 word label that tells viewers what the video is about.\n"
                    "Rules: No punctuation. No quotes. ALL CAPS. Max 25 characters.\n"
                    "Output ONLY the label text, nothing else."
                )
                topic_label = _query_llm(self.config, system_prompt, f"Condense: {raw_topic}", task="shorts_selection").strip().strip('"').strip("'")
                if not topic_label or len(topic_label) > 40:
                    topic_label = raw_topic[:35]
            except Exception as e:
                print(f"[Short Generator] LLM topic label failed: {e}, using truncated topic")
                topic_label = raw_topic[:35]

            topic_overlay_path = self._render_topic_overlay(topic_label, width, height, short_temp_dir)

        scene_mp4_files = []

        # Compile vertical scenes
        for idx in range(start_idx, end_idx + 1):
            voice = scenes_voice[idx]
            visual = scenes_visual[idx]

            image_file = visual["image_file"]
            audio_file = voice["audio_file"]
            duration = voice["duration"]
            spoken_text = voice["spoken_text"]
            word_srt_file = voice.get("word_srt_file")

            is_video = image_file.lower().endswith(".mp4")
            ext = ".mp4" if is_video else ".jpg"

            rel_visual_name = f"visual_{idx}{ext}"
            rel_audio_name = f"audio_{idx}.mp3"
            rel_srt_name = f"subtitles_{idx}.srt"
            rel_mp4_name = f"scene_{idx}.mp4"

            # Copy inputs
            shutil.copy(image_file, os.path.join(short_temp_dir, rel_visual_name))
            shutil.copy(audio_file, os.path.join(short_temp_dir, rel_audio_name))

            # Write srt
            srt_full_path = os.path.join(short_temp_dir, rel_srt_name)
            if word_srt_file and os.path.exists(word_srt_file):
                self.write_scene_srt_timed(word_srt_file, srt_full_path)
            else:
                self.write_scene_srt(spoken_text, duration, srt_full_path)

            # High-impact subtitles centered vertically for vertical format
            # Yellow text & outline. Alignment 2 (bottom center), MarginV 360 (safe zone)
            subtitle_filter = f"subtitles={rel_srt_name}:force_style=FontName=Arial Black\\,FontSize=36\\,Bold=1\\,PrimaryColour=&H0000FFFF\\,OutlineColour=&H00000000\\,BorderStyle=1\\,Outline=3\\,Shadow=0\\,Alignment=2\\,MarginV=360"

            # Check if we should add topic overlay (first scene only)
            use_overlay = (idx == start_idx and topic_overlay_path and os.path.exists(topic_overlay_path))

            if is_video:
                # Scale to COVER 1080x1920 (object-fit: cover), then center crop.
                video_scale_crop = (
                    f"scale=if(gt(a\\,{width}/{height})\\,{height}*dar\\,{width})"
                    f":if(gt(a\\,{width}/{height})\\,{height}\\,{width}/dar),"
                    f"setsar=1,"
                    f"crop={width}:{height}:(in_w-{width})/2:(in_h-{height})/2"
                )
                combined_filter = f"{video_scale_crop},{subtitle_filter}"

                if use_overlay:
                    overlay_input = "topic_overlay.png"
                    cmd = [
                        "ffmpeg", "-y",
                        "-stream_loop", "-1",
                        "-i", rel_visual_name,
                        "-i", rel_audio_name,
                        "-i", overlay_input,
                        "-filter_complex", f"[0:v]{video_scale_crop},{subtitle_filter}[base];[base][2:v]overlay=0:0:enable=between(t\\,0.3\\,2.8)[v]",
                        "-map", "[v]",
                        "-map", "1:a",
                        "-c:v", "libx264",
                        "-r", str(fps),
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-pix_fmt", "yuv420p",
                        "-t", f"{duration:.3f}",
                        rel_mp4_name
                    ]
                else:
                    cmd = [
                        "ffmpeg", "-y",
                        "-stream_loop", "-1",
                        "-i", rel_visual_name,
                        "-i", rel_audio_name,
                        "-filter_complex", f"[0:v]{combined_filter}[v]",
                        "-map", "[v]",
                        "-map", "1:a",
                        "-c:v", "libx264",
                        "-r", str(fps),
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-pix_fmt", "yuv420p",
                        "-t", f"{duration:.3f}",
                        rel_mp4_name
                    ]
            else:
                # Still image → portrait fill with slow upward Ken Burns pan.
                image_scale_crop = (
                    f"scale=if(gt(a\\,{width}/{height})\\,{height}*dar\\,{width})"
                    f":if(gt(a\\,{width}/{height})\\,{height}\\,{width}/dar),"
                    f"setsar=1,"
                    f"crop={width}:{height}:(in_w-{width})/2:max(((in_h-{height})/2)-t*4,0)"
                )
                combined_filter = f"{image_scale_crop},{subtitle_filter}"

                if use_overlay:
                    overlay_input = "topic_overlay.png"
                    cmd = [
                        "ffmpeg", "-y",
                        "-loop", "1", "-i", rel_visual_name,
                        "-i", rel_audio_name,
                        "-i", overlay_input,
                        "-filter_complex", f"[0:v]{image_scale_crop},{subtitle_filter}[base];[base][2:v]overlay=0:0:enable=between(t\\,0.3\\,2.8)[v]",
                        "-map", "[v]",
                        "-map", "1:a",
                        "-c:v", "libx264",
                        "-r", str(fps),
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-pix_fmt", "yuv420p",
                        "-t", f"{duration:.3f}",
                        rel_mp4_name
                    ]
                else:
                    cmd = [
                        "ffmpeg", "-y",
                        "-loop", "1", "-i", rel_visual_name,
                        "-i", rel_audio_name,
                        "-vf", combined_filter,
                        "-c:v", "libx264",
                        "-r", str(fps),
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-pix_fmt", "yuv420p",
                        "-t", f"{duration:.3f}",
                        rel_mp4_name
                    ]

            subprocess.run(cmd, cwd=short_temp_dir, capture_output=True, text=True, check=True)
            scene_mp4_files.append(rel_mp4_name)

        # Concat
        concat_list_path = os.path.join(short_temp_dir, "concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for f_name in scene_mp4_files:
                f.write(f"file '{f_name}'\n")

        temp_short_unmixed_name = "final_short_temp.mp4"
        temp_short_unmixed_path = os.path.join(run_dir, temp_short_unmixed_name)
        final_short_path = os.path.join(run_dir, "final_short.mp4")

        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", "concat_list.txt",
            "-c", "copy",
            f"../{temp_short_unmixed_name}"
        ]
        subprocess.run(concat_cmd, cwd=short_temp_dir, capture_output=True, text=True, check=True)

        # Mix bgm
        bg_music_file = state["steps"].get("AUDIO_GEN", {}).get("output", {}).get("bg_music_file")
        audio_settings = self.config.get("audio_settings", {})

        if bg_music_file and os.path.exists(bg_music_file):
            print(f"[Short Generator] Mixing BGM for Short...")
            # For shorts, mix BGM volume slightly louder (e.g. +33% increase over horizontal)
            bg_vol = audio_settings.get("bg_music_volume", 0.15) * 1.33
            thresh = audio_settings.get("ducking_threshold", 0.10)
            ratio = audio_settings.get("ducking_ratio", 4.0)
            attack = audio_settings.get("ducking_attack", 200)
            release = audio_settings.get("ducking_release", 800)

            filter_complex = (
                f"[1:a]volume={bg_vol}[bg_music];"
                f"[0:a]asplit=2[sc][voice];"
                f"[bg_music][sc]sidechaincompress=threshold={thresh}:ratio={ratio}:attack={attack}:release={release}[ducked_bg];"
                f"[voice][ducked_bg]amix=inputs=2:duration=first[mixed_audio]"
            )

            mix_cmd = [
                "ffmpeg", "-y",
                "-i", temp_short_unmixed_path,
                "-stream_loop", "-1",
                "-i", bg_music_file,
                "-filter_complex", filter_complex,
                "-map", "0:v",
                "-map", "[mixed_audio]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                final_short_path
            ]
            subprocess.run(mix_cmd, capture_output=True, text=True, check=True)
            if os.path.exists(temp_short_unmixed_path):
                os.remove(temp_short_unmixed_path)
        else:
            shutil.move(temp_short_unmixed_path, final_short_path)

        # Clean up temporary dir
        try:
            shutil.rmtree(short_temp_dir)
        except Exception:
            pass

        # Update run_state
        short_metadata = {
            "status": "SUCCESS",
            "start_scene_index": start_idx,
            "end_scene_index": end_idx,
            "reason": reason,
            "video_file": final_short_path,
            "duration": total_duration
        }
        
        if orchestrator:
            orchestrator.update_run_state(run_dir, {"short": short_metadata})
        else:
            state["short"] = short_metadata
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)

        return short_metadata
