import os
import json
import subprocess

class VideoAssemblerAgent:
    def __init__(self, config):
        self.config = config

    def format_srt_time(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def write_scene_srt_timed(self, word_srt_path, srt_out_path, time_offset=0, time_end=None):
        """Parse and group the word-level SRT into clean multi-word subtitle segments.
        If time_offset > 0 or time_end is set, only include words within that time window
        and shift timestamps to start from 0 (for sub-segment rendering)."""
        import re
        
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

        # Find all SRT blocks
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
                # Filter to time window
                if end <= time_offset:
                    continue
                if time_end is not None and start >= time_end:
                    continue
                words.append({"word": word, "start": start, "end": end})
            except Exception as e:
                print(f"[Video Agent] Error parsing block {idx}: {e}")

        if not words:
            # Fallback to direct copy if parsing failed
            import shutil
            shutil.copy(word_srt_path, srt_out_path)
            return

        # Shift timestamps to start from 0
        if time_offset > 0:
            for w in words:
                w["start"] = max(0, w["start"] - time_offset)
                w["end"] = max(0, w["end"] - time_offset)
            if time_end is not None:
                max_time = time_end - time_offset
                words = [w for w in words if w["start"] < max_time]

        # Grouping constraints
        max_words = 5
        max_duration = 2.5
        max_gap = 0.3

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

        # Write grouped SRT file
        with open(srt_out_path, "w", encoding="utf-8") as f:
            for idx, group in enumerate(grouped_segments):
                start_time = group[0]["start"]
                end_time = group[-1]["end"]

                words_in_group = [g["word"] for g in group]
                if len(words_in_group) > 3:
                    mid = len(words_in_group) // 2
                    line1 = " ".join(words_in_group[:mid])
                    line2 = " ".join(words_in_group[mid:])
                    display_text = f"{line1}\n{line2}"
                else:
                    display_text = " ".join(words_in_group)

                f.write(f"{idx + 1}\n")
                f.write(f"{format_srt_time(start_time)} --> {format_srt_time(end_time)}\n")
                f.write(f"{display_text}\n\n")

    def normalize_bumper(self, input_path, output_path, target_width, target_height, target_fps):
        """Normalizes bumper video to match scene settings exactly, preventing timeline desync on concat."""
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-r", str(target_fps),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-ar", "24000",
            "-ac", "1",
            "-b:a", "192k",
            output_path
        ]
        try:
            print(f"[Video Agent] Normalizing bumper {os.path.basename(input_path)} to {target_width}x{target_height} at {target_fps} fps...")
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True
        except Exception as e:
            print(f"[Video Agent] Failed to normalize bumper {input_path}: {e}")
            import shutil
            shutil.copy(input_path, output_path)
            return False

    def write_scene_srt(self, text, duration, srt_path, time_offset=0, time_end=None):
        """Fallback: estimate subtitle timing from words-per-second rate.
        If time_offset > 0 or time_end is set, only render subtitles within that window."""
        words = text.split()
        if not words:
            return

        words_per_second = len(words) / duration if duration > 0 else 2.5

        words_per_segment = 6
        segments = []
        for i in range(0, len(words), words_per_segment):
            segments.append(words[i:i+words_per_segment])

        video_settings = self.config.get("video_settings", {})
        subtitle_delay = video_settings.get("subtitle_delay", 0.0)

        effective_duration = (time_end - time_offset) if time_end is not None else duration

        with open(srt_path, "w", encoding="utf-8") as f:
            elapsed_words = 0
            srt_idx = 1
            for idx, segment in enumerate(segments):
                raw_start = (elapsed_words / words_per_second)
                raw_end = ((elapsed_words + len(segment)) / words_per_second)

                # Apply time window filtering
                if time_end is not None:
                    if raw_end <= time_offset or raw_start >= time_end:
                        elapsed_words += len(segment)
                        continue
                    # Clip to window
                    seg_start = max(0, raw_start - time_offset)
                    seg_end = min(effective_duration, raw_end - time_offset)
                else:
                    seg_start = subtitle_delay + raw_start
                    seg_end = subtitle_delay + raw_end

                seg_end = min(seg_end, effective_duration)
                if seg_start >= effective_duration:
                    break

                start_str = self.format_srt_time(seg_start)
                end_str = self.format_srt_time(seg_end)

                midpoint = len(segment) // 2
                line1 = " ".join(segment[:midpoint])
                line2 = " ".join(segment[midpoint:])
                display_text = f"{line1}\n{line2}" if line2 else line1

                f.write(f"{srt_idx}\n")
                f.write(f"{start_str} --> {end_str}\n")
                f.write(f"{display_text}\n\n")
                srt_idx += 1

                elapsed_words += len(segment)

    def run(self, inputs):
        run_dir = inputs.get("run_dir")
        
        # Load voice metadata and visual metadata directly to get synced items
        voice_meta_path = os.path.join(run_dir, "voice_metadata.json")
        visual_meta_path = os.path.join(run_dir, "visual_metadata.json")

        if not os.path.exists(voice_meta_path) or not os.path.exists(visual_meta_path):
            raise FileNotFoundError("Voice metadata or Visual metadata is missing from the run folder.")

        with open(voice_meta_path, "r", encoding="utf-8") as f:
            voice_meta = json.load(f)
        with open(visual_meta_path, "r", encoding="utf-8") as f:
            visual_meta = json.load(f)

        scenes_voice = voice_meta.get("scenes", [])
        scenes_visual = visual_meta.get("visual_assets", [])

        # Create output directories
        video_temp_dir = os.path.join(run_dir, "video_temp")
        os.makedirs(video_temp_dir, exist_ok=True)

        video_settings = self.config.get("video_settings", {})
        width = video_settings.get("width", 1920)
        height = video_settings.get("height", 1080)
        fps = video_settings.get("fps", 30)

        # We will create individual scene MP4s
        scene_mp4_files = []

        for idx, (voice, visual) in enumerate(zip(scenes_voice, scenes_visual)):
            image_file = visual["image_file"]
            audio_file = voice["audio_file"]
            duration = voice["duration"]
            spoken_text = voice["spoken_text"]
            word_srt_file = voice.get("word_srt_file")  # Word-level SRT from edge_tts

            # Check if visual file is mp4 video or still image
            is_video = image_file.lower().endswith(".mp4")
            ext = ".mp4" if is_video else ".jpg"
            
            rel_visual_name = f"visual_{idx}{ext}"
            rel_audio_name = f"audio_{idx}.mp3"
            rel_srt_name = f"subtitles_{idx}.srt"
            rel_mp4_name = f"scene_{idx}.mp4"

            # Copy inputs into the video_temp_dir
            import shutil
            shutil.copy(image_file, os.path.join(video_temp_dir, rel_visual_name))
            shutil.copy(audio_file, os.path.join(video_temp_dir, rel_audio_name))

            # Write subtitles — prefer word-level SRT from edge_tts, fall back to rate-based estimation
            srt_full_path = os.path.join(video_temp_dir, rel_srt_name)
            if word_srt_file and os.path.exists(word_srt_file):
                self.write_scene_srt_timed(word_srt_file, srt_full_path)
            else:
                self.write_scene_srt(spoken_text, duration, srt_full_path)

            # Subtitle styling - modern, clean, high readability
            subtitle_filter = f"subtitles={rel_srt_name}:force_style='FontName=Montserrat,FontSize=22,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=1,Outline=2,Shadow=2,Alignment=2,MarginV=50,MarginL=60,MarginR=60'"

            # Q2: Scene pacing — use multiple clips for long scenes
            extra_clips = visual.get("extra_clips", [])
            use_pacing = is_video and len(extra_clips) > 0 and duration > 3

            if use_pacing:
                # Split scene into sub-segments using different clips
                all_clips = [image_file] + extra_clips
                num_clips = len(all_clips)
                segment_duration = duration / num_clips
                sub_scene_files = []

                for seg_idx, clip_path in enumerate(all_clips):
                    seg_start = seg_idx * segment_duration
                    seg_end = min((seg_idx + 1) * segment_duration, duration)
                    seg_dur = seg_end - seg_start

                    # Copy clip to temp dir
                    seg_clip_name = f"seg_clip_{idx}_{seg_idx}.mp4"
                    shutil.copy(clip_path, os.path.join(video_temp_dir, seg_clip_name))

                    # Build SRT for this segment
                    seg_srt_name = f"seg_sub_{idx}_{seg_idx}.srt"
                    seg_srt_path = os.path.join(video_temp_dir, seg_srt_name)
                    if word_srt_file and os.path.exists(word_srt_file):
                        self.write_scene_srt_timed(
                            word_srt_file, seg_srt_path,
                            time_offset=seg_start, time_end=seg_end
                        )
                    else:
                        self.write_scene_srt(spoken_text, duration, seg_srt_path,
                                             time_offset=seg_start, time_end=seg_end)

                    seg_subtitle_filter = f"subtitles={seg_srt_name}:force_style='FontName=Montserrat,FontSize=22,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=1,Outline=2,Shadow=2,Alignment=2,MarginV=50,MarginL=60,MarginR=60'"
                    video_scale_crop = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
                    combined_filter = f"{video_scale_crop},{seg_subtitle_filter}"

                    # Use different seek points for variety: 0s, 3s, 6s, ...
                    seek_point = (seg_idx * 3) % 10  # cycle through 0,3,6,9,2,5,...

                    seg_mp4_name = f"seg_scene_{idx}_{seg_idx}.mp4"
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(seek_point),
                        "-stream_loop", "-1",
                        "-i", seg_clip_name,
                        "-ss", f"{seg_start:.3f}",
                        "-i", rel_audio_name,
                        "-filter_complex", f"[0:v]{combined_filter}[v]",
                        "-map", "[v]", "-map", "1:a",
                        "-c:v", "libx264", "-r", str(fps),
                        "-c:a", "aac", "-b:a", "192k",
                        "-pix_fmt", "yuv420p",
                        "-t", f"{seg_dur:.3f}",
                        seg_mp4_name
                    ]
                    subprocess.run(cmd, cwd=video_temp_dir, capture_output=True, text=True, check=True)
                    sub_scene_files.append(seg_mp4_name)

                # Concatenate sub-segments into the final scene file
                if len(sub_scene_files) == 1:
                    shutil.copy(
                        os.path.join(video_temp_dir, sub_scene_files[0]),
                        os.path.join(video_temp_dir, rel_mp4_name)
                    )
                else:
                    # Build a simple concat list for sub-segments
                    with open(os.path.join(video_temp_dir, "seg_concat.txt"), "w", encoding="utf-8") as f:
                        for sf in sub_scene_files:
                            f.write(f"file '{sf}'\n")
                    sub_concat_cmd = [
                        "ffmpeg", "-y",
                        "-f", "concat", "-safe", "0",
                        "-i", "seg_concat.txt",
                        "-c", "copy",
                        rel_mp4_name
                    ]
                    subprocess.run(sub_concat_cmd, cwd=video_temp_dir, capture_output=True, text=True, check=True)

            elif is_video:
                # Loop video infinitely, scale to fill & crop, then map subtitle on top
                video_scale_crop = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
                combined_filter = f"{video_scale_crop},{subtitle_filter}"
                
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
                # Apply Ken Burns effect (zoom/drift) for still images
                scale_width = int(width * 1.2)
                scale_height = int(height * 1.2)
                image_scale_crop = f"scale={scale_width}:{scale_height},crop={width}:{height}:'(in_w-out_w)/2':'max((in_h-out_h)/2-t*4,0)'"
                combined_filter = f"{image_scale_crop},{subtitle_filter}"
                
                cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", rel_visual_name,
                    "-i", rel_audio_name,
                    "-vf", combined_filter,
                    "-c:v", "libx264",
                    "-r", str(fps),
                    "-tune", "stillimage",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    "-t", f"{duration:.3f}",
                    rel_mp4_name
                ]
            
            # Run in the context of the temp folder
            subprocess.run(cmd, cwd=video_temp_dir, capture_output=True, text=True, check=True)
            scene_mp4_files.append(rel_mp4_name)

        # Concatenate all rendered scenes
        # Check if assets/intro.mp4 and assets/outro.mp4 exist, and copy them to prepend/append automatically
        import shutil
        workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assets_dir = os.path.join(workspace_dir, "assets")
        
        intro_file = os.path.join(assets_dir, "intro.mp4")
        outro_file = os.path.join(assets_dir, "outro.mp4")
        
        video_list = []
        if os.path.exists(intro_file):
            intro_bumper_path = os.path.join(video_temp_dir, "intro_bumper.mp4")
            self.normalize_bumper(intro_file, intro_bumper_path, width, height, fps)
            video_list.append("intro_bumper.mp4")
            
        video_list.extend(scene_mp4_files)
        
        if os.path.exists(outro_file):
            outro_bumper_path = os.path.join(video_temp_dir, "outro_bumper.mp4")
            self.normalize_bumper(outro_file, outro_bumper_path, width, height, fps)
            video_list.append("outro_bumper.mp4")

        # Normalize audio in each video file to consistent format before concat
        normalized_video_list = []
        for i, v_name in enumerate(video_list):
            norm_name = f"norm_{v_name}"
            norm_path = os.path.join(video_temp_dir, norm_name)
            src_path = os.path.join(video_temp_dir, v_name)
            # Normalize audio: 44100Hz, mono, 192k AAC
            norm_cmd = [
                "ffmpeg", "-y",
                "-i", src_path,
                "-c:v", "copy",
                "-af", "aresample=44100,pan=mono|c0=0.5*c0+0.5*c1",
                "-c:a", "aac",
                "-b:a", "192k",
                norm_path
            ]
            print(f"[Video Agent] Normalizing audio for {v_name}...")
            subprocess.run(norm_cmd, cwd=video_temp_dir, capture_output=True, text=True, check=True, timeout=60)
            normalized_video_list.append(norm_name)
        
        concat_list_path = os.path.join(video_temp_dir, "concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for f_name in normalized_video_list:
                f.write(f"file '{f_name}'\n")

        final_video_name = "final_output.mp4"
        final_video_path = os.path.join(run_dir, final_video_name)
        temp_video_name = "final_output_temp.mp4"
        temp_video_path = os.path.join(run_dir, temp_video_name)

        final_video_name = "final_output.mp4"
        final_video_path = os.path.join(run_dir, final_video_name)
        temp_video_name = "final_output_temp.mp4"
        temp_video_path = os.path.join(run_dir, temp_video_name)

        # Use simple concat demuxer - reliable and fast
        if len(video_list) > 1:

            concat_cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", "concat_list.txt",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                f"../{temp_video_name}"
            ]
            print("[Video Agent] Concatenating scenes with concat demuxer...")
            subprocess.run(concat_cmd, cwd=video_temp_dir, capture_output=True, text=True, check=True)
        else:
            # Single video, just copy
            shutil.copy2(os.path.join(video_temp_dir, video_list[0]), temp_video_path)

        # Mix background music if configured/provided
        bg_music_file = inputs.get("bg_music_file")
        audio_settings = self.config.get("audio_settings", {})
        
        if bg_music_file and os.path.exists(bg_music_file):
            print(f"[Video Agent] Mixing background music from {bg_music_file}...")
            
            # Fetch sidechain/ducking parameters
            bg_vol = audio_settings.get("bg_music_volume", 0.15)
            thresh = audio_settings.get("ducking_threshold", 0.10)
            ratio = audio_settings.get("ducking_ratio", 4.0)
            attack = audio_settings.get("ducking_attack", 200)
            release = audio_settings.get("ducking_release", 800)
            
            # Sidechain compress filter graph:
            # 1. Scale background music input volume [1:a]
            # 2. Split main video voiceover audio [0:a] into [sc] and [voice]
            # 3. Apply sidechaincompress filter using [sc] to duck [bg_music]
            # 4. Mix ducked background music and voiceover using amix
            filter_complex = (
                f"[1:a]volume={bg_vol}[bg_music];"
                f"[0:a]asplit=2[sc][voice];"
                f"[bg_music][sc]sidechaincompress=threshold={thresh}:ratio={ratio}:attack={attack}:release={release}[ducked_bg];"
                f"[voice][ducked_bg]amix=inputs=2:duration=first[mixed_audio]"
            )
            
            mix_cmd = [
                "ffmpeg", "-y",
                "-i", temp_video_path,
                "-stream_loop", "-1",
                "-i", bg_music_file,
                "-filter_complex", filter_complex,
                "-map", "0:v",
                "-map", "[mixed_audio]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                final_video_path
            ]
            
            print(f"[Video Agent] Running FFmpeg BGM sidechain compress mix command...")
            subprocess.run(mix_cmd, capture_output=True, text=True, check=True)
            
            # Clean up temp file
            if os.path.exists(temp_video_path):
                os.remove(temp_video_path)
        else:
            print("[Video Agent] No background music configured. Skipping mix pass.")
            if os.path.exists(final_video_path):
                os.remove(final_video_path)
            shutil.move(temp_video_path, final_video_path)

        # Cleanup temp dir if necessary, or leave it for inspection
        # For robustness, we will keep it but save the path of the final MP4
        output_metadata = {
            "video_file": final_video_path,
            "width": width,
            "height": height,
            "duration": voice_meta.get("duration"),
            "status": "SUCCESS"
        }

        # Save assembly metadata in the run directory
        assembly_meta_path = os.path.join(run_dir, "assembly_metadata.json")
        with open(assembly_meta_path, "w", encoding="utf-8") as f:
            json.dump(output_metadata, f, indent=2)

        return output_metadata
