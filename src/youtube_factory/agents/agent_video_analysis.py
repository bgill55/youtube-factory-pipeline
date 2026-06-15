"""
VideoAnalysisAgent - Analyzes screen recordings to extract segments, 
transcripts, and visual context for Asset-First video pipeline.
"""

import os
import json
import subprocess
import tempfile
import shutil
from datetime import datetime
from typing import List, Dict, Any, Optional

# Tesseract OCR path
# Tesseract OCR path - configurable via config.json with fallback
TESSERACT_CMD = None  # Will be set from config in VideoAnalysisAgent.__init__

class VideoAnalysisAgent:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.whisper_model = self.config.get("whisper_model", "base")
        self.scene_threshold = self.config.get("scene_threshold", 0.3)
        self.min_segment_duration = self.config.get("min_segment_duration", 2.0)
        self.max_segment_duration = self.config.get("max_segment_duration", 60.0)
        
        # Tesseract OCR path - from config or common defaults
        self.tesseract_cmd = self.config.get(
            "tesseract_cmd",
            r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        )
        
    def analyze(self, video_path: str, seed_topic: str = "", run_dir: str = None) -> Dict[str, Any]:
        """
        Main analysis pipeline:
        1. Extract audio for transcription
        2. Detect scene changes (visual cuts)
        3. Transcribe audio with Whisper
        4. Align scenes with transcript
        5. Classify segments and generate metadata
        """
        print(f"[VideoAnalysis] Analyzing: {video_path}")
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")
        
        # Get video info
        video_info = self._get_video_info(video_path)
        duration = video_info["duration"]
        print(f"[VideoAnalysis] Duration: {duration:.1f}s, {video_info['width']}x{video_info['height']} @ {video_info['fps']:.1f}fps")
        
        # Step 1: Scene detection
        scene_times = self._detect_scenes(video_path)
        print(f"[VideoAnalysis] Detected {len(scene_times)-1} scenes")
        
        # Step 2: Extract audio and transcribe
        transcript_segments = self._transcribe_audio(video_path)
        print(f"[VideoAnalysis] Transcribed {len(transcript_segments)} segments")
        
        # Step 3: Build unified segments (align scenes + transcript)
        segments = self._build_segments(scene_times, transcript_segments, duration)
        print(f"[VideoAnalysis] Built {len(segments)} unified segments")
        
        # Step 4: Classify and enrich each segment
        enriched_segments = []
        for seg in segments:
            enriched = self._enrich_segment(seg, video_path, seed_topic)
            enriched_segments.append(enriched)
        
        # Step 5: Generate suggested script structure
        script_structure = self._generate_script_structure(enriched_segments, seed_topic)
        
        result = {
            "video_path": video_path,
            "video_info": video_info,
            "seed_topic": seed_topic,
            "analyzed_at": datetime.now().isoformat(),
            "total_duration": duration,
            "scene_count": len(scene_times) - 1,
            "segments": enriched_segments,
            "script_structure": script_structure
        }
        
        # Save to run_dir if provided
        if run_dir:
            output_path = os.path.join(run_dir, "video_analysis.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"[VideoAnalysis] Saved to: {output_path}")
        
        return result
    
    def _get_video_info(self, video_path: str) -> Dict[str, Any]:
        """Get video metadata via ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration,r_frame_rate,codec_name",
            "-of", "json", video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr}")
        
        info = json.loads(result.stdout)
        stream = info["streams"][0]
        
        # Parse frame rate
        fps_str = stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, den = map(int, fps_str.split("/"))
            fps = num / den if den else 30
        else:
            fps = float(fps_str)
        
        return {
            "width": stream.get("width", 1920),
            "height": stream.get("height", 1080),
            "duration": float(stream.get("duration", 0)),
            "fps": fps,
            "codec": stream.get("codec_name", "unknown")
        }
    
    def _detect_scenes(self, video_path: str) -> List[float]:
        """
        Detect scene changes using FFmpeg's scene detection filter.
        Returns list of timestamps [0.0, t1, t2, ..., duration]
        """
        # Use ffmpeg scene detection
        # select='gt(scene,0.3)' outputs frames where scene change > threshold
        # We use showinfo to get timestamps
        cmd = [
            "ffmpeg", "-v", "error",
            "-i", video_path,
            "-filter:v", f"select='gt(scene,{self.scene_threshold})',showinfo",
            "-f", "null", "-"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        # Parse showinfo output for timestamps
        # Format: pts_time:1.234
        timestamps = [0.0]
        for line in result.stderr.split("\n"):
            if "pts_time:" in line:
                try:
                    t = float(line.split("pts_time:")[1].split()[0])
                    if t > 0:
                        timestamps.append(t)
                except (ValueError, IndexError):
                    pass
        
        # Get total duration for final timestamp
        info = self._get_video_info(video_path)
        timestamps.append(info["duration"])
        
        # Filter: merge segments too close together, split too long ones
        filtered = [timestamps[0]]
        for t in timestamps[1:]:
            if t - filtered[-1] >= self.min_segment_duration:
                filtered.append(t)
            else:
                # Merge with previous - use midpoint
                filtered[-1] = (filtered[-1] + t) / 2
        
        # Ensure we don't exceed max_segment_duration by splitting long segments
        final = [filtered[0]]
        for t in filtered[1:]:
            while t - final[-1] > self.max_segment_duration:
                final.append(final[-1] + self.max_segment_duration)
            final.append(t)
        
        return final
    
    def _transcribe_audio(self, video_path: str) -> List[Dict[str, Any]]:
        """Extract audio and transcribe with Whisper."""
        import whisper
        
        # Extract audio to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            audio_path = tmp.name
        
        try:
            # Extract audio: 16kHz mono for Whisper
            cmd = [
                "ffmpeg", "-v", "error", "-y",
                "-i", video_path,
                "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le",
                audio_path
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                print(f"[VideoAnalysis] Audio extraction failed: {result.stderr[:200]}")
                return []
            
            # Transcribe with Whisper
            print(f"[VideoAnalysis] Loading Whisper model ({self.whisper_model})...")
            # Suppress Triton CUDA warnings from Whisper
            import warnings
            warnings.filterwarnings("ignore", message="Failed to launch Triton kernels")
            warnings.filterwarnings("ignore", message="failed to launch Triton kernels")
            # Also suppress via environment
            import os
            os.environ.setdefault("TRITON_CACHE_DIR", os.path.join(tempfile.gettempdir(), "triton_cache"))
            model = whisper.load_model(self.whisper_model)
            print(f"[VideoAnalysis] Transcribing...")
            result = model.transcribe(audio_path, verbose=False, word_timestamps=True)
            
            segments = []
            for seg in result.get("segments", []):
                segments.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip(),
                    "words": seg.get("words", [])  # word-level timestamps
                })
            
            return segments
            
        except Exception as e:
            print(f"[VideoAnalysis] Transcription failed: {e}")
            return []
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
    
    def _build_segments(self, scene_times: List[float], transcript_segments: List[Dict], total_duration: float) -> List[Dict]:
        """
        Align visual scenes with transcript segments.
        Creates unified segments covering the full video.
        """
        segments = []
        
        for i in range(len(scene_times) - 1):
            scene_start = scene_times[i]
            scene_end = scene_times[i + 1]
            
            # Find overlapping transcript segments
            overlapping = []
            for ts in transcript_segments:
                if ts["end"] > scene_start and ts["start"] < scene_end:
                    # Calculate overlap
                    overlap_start = max(scene_start, ts["start"])
                    overlap_end = min(scene_end, ts["end"])
                    overlap_duration = overlap_end - overlap_start
                    if overlap_duration > 0:
                        overlapping.append({
                            "text": ts["text"],
                            "start": ts["start"],
                            "end": ts["end"],
                            "overlap_ratio": overlap_duration / (scene_end - scene_start)
                        })
            
            # Combine transcript text
            transcript_text = " ".join([o["text"] for o in overlapping]) if overlapping else ""
            
            segments.append({
                "index": i,
                "start": scene_start,
                "end": scene_end,
                "duration": scene_end - scene_start,
                "transcript": transcript_text,
                "transcript_segments": overlapping,
                "has_audio": len(overlapping) > 0
            })
        
        return segments
    
    def _enrich_segment(self, segment: Dict, video_path: str, seed_topic: str) -> Dict:
        """
        Analyze segment content: classify action type, extract keyframes,
        generate visual description, suggest narration.
        Uses OCR on keyframes for visual classification.
        """
        start = segment["start"]
        end = segment["end"]
        duration = segment["duration"]
        transcript = segment["transcript"]
        
        # Extract middle frame for visual analysis
        keyframe_path = self._extract_keyframe(video_path, (start + end) / 2)
        
        # Run OCR on keyframe for visual classification
        ocr_text = self._ocr_keyframe(keyframe_path)
        
        # Classify action type based on transcript + OCR + heuristics
        action_type = self._classify_action(transcript, ocr_text, duration)
        
        # Generate visual description
        visual_desc = self._generate_visual_description(transcript, ocr_text, action_type, duration)
        
        # Suggest narration (use transcript if speaker is narrator, else generate)
        suggested_narration = self._suggest_narration(transcript, ocr_text, action_type, visual_desc, seed_topic)
        
        # Determine if this segment should be kept as-is or needs bridge content
        role = "asset" if transcript or action_type in ("terminal", "code", "demo", "ui", "browser", "ide") else "bridge"
        
        enriched = {
            **segment,
            "keyframe": keyframe_path,
            "ocr_text": ocr_text,
            "action_type": action_type,
            "visual_description": visual_desc,
            "suggested_narration": suggested_narration,
            "role": role,  # "asset" = user content, "bridge" = generated
            "needs_transition": False  # will be set in script_structure
        }
        
        return enriched
    
    def _extract_keyframe(self, video_path: str, timestamp: float) -> str:
        """Extract a single frame at timestamp for visual analysis."""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = tmp.name
        
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-ss", str(timestamp),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            frame_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode == 0 and os.path.exists(frame_path):
            return frame_path
        return ""
    
    def _ocr_keyframe(self, keyframe_path: str) -> str:
        """Extract text from keyframe using Tesseract OCR."""
        if not keyframe_path or not os.path.exists(keyframe_path):
            return ""
        try:
            cmd = [self.tesseract_cmd, keyframe_path, "stdout", "-l", "eng", "--psm", "6"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            print(f"[VideoAnalysis] OCR failed: {e}")
        return ""

    def _classify_action(self, transcript: str, ocr_text: str, duration: float) -> str:
        """Classify what's happening using transcript + OCR text."""
        # Combine transcript and OCR for analysis
        combined = (transcript + " " + ocr_text).lower()
        
        # Terminal/command line - OCR catches prompt, commands, output
        terminal_keywords = ["$", ">", "bash", "shell", "cmd", "powershell", "terminal", "command", "git ", "npm ", "pip ", "python ", "cd ", "ls ", "mkdir", "curl", "wget", "docker", "kubectl"]
        if any(kw in combined for kw in terminal_keywords):
            return "terminal"
        
        # Code/IDE - OCR catches syntax, braces, keywords
        code_keywords = ["function", "class ", "def ", "import ", "const ", "let ", "var ", "return ", "if (", "for (", "while (", "=>", "===", "===", "null", "undefined", "async", "await", "try {", "catch", "public ", "private ", "static ", "void "]
        if any(kw in combined for kw in code_keywords):
            return "code"
        
        # Browser - OCR catches URL bar, tabs, web UI
        browser_keywords = ["http://", "https://", "github.com", "chromium", "chrome", "firefox", "edge", "localhost:", "127.0.0.1", "address bar", "tab", "bookmark"]
        if any(kw in combined for kw in browser_keywords):
            return "browser"
        
        # IDE/Editor - OCR catches line numbers, file tabs, sidebar
        ide_keywords = ["vscode", "visual studio", "intellij", "pycharm", "line ", "col ", "problems", "terminal", "debug", "run ", "extensions", "explorer", "outline"]
        if any(kw in combined for kw in ide_keywords):
            return "ide"
        
        # UI/Application - OCR catches buttons, menus, dialogs
        ui_keywords = ["button", "menu", "settings", "dashboard", "window", "dialog", "click", "submit", "cancel", "ok", "apply", "preferences", "configuration"]
        if any(kw in combined for kw in ui_keywords):
            return "ui"
        
        # Demo/walkthrough
        demo_keywords = ["show", "demo", "walkthrough", "tutorial", "step", "here", "this is", "now we", "next", "then", "finally"]
        if any(kw in combined for kw in demo_keywords):
            return "demo"
        
        # Explanation/teaching
        explain_keywords = ["explain", "because", "reason", "why", "how", "what is", "means", "concept", "understand", "basically", "essentially"]
        if any(kw in combined for kw in explain_keywords):
            return "explanation"
        
        # Pure visual (no speech, no readable text)
        if not transcript.strip() and not ocr_text.strip():
            return "visual"
        
        return "talking"
    
    def _generate_visual_description(self, transcript: str, ocr_text: str, action_type: str, duration: float) -> str:
        """Generate a visual description using transcript + OCR."""
        # Use OCR text for specific description
        if action_type == "terminal":
            # Extract actual commands from OCR
            lines = [l for l in ocr_text.split("\n") if l.strip() and ("$" in l or ">" in l)]
            if lines:
                return f"Terminal: {lines[0][:60]}..."
            return "Terminal session with commands and output"
        elif action_type == "code":
            # Detect language from OCR
            if "def " in ocr_text or "import " in ocr_text:
                lang = "Python"
            elif "function " in ocr_text or "const " in ocr_text:
                lang = "JavaScript/TypeScript"
            elif "public " in ocr_text or "class " in ocr_text:
                lang = "Java/C#"
            else:
                lang = "code"
            return f"{lang} editor with syntax highlighting"
        elif action_type == "browser":
            return "Web browser navigation"
        elif action_type == "ide":
            return "IDE with code editor and terminal"
        elif action_type == "terminal":
            return "Terminal session with commands and output"
        elif action_type == "ui":
            return "Application UI interaction"
        elif action_type == "demo":
            return "Step-by-step demonstration"
        elif action_type == "explanation":
            return "Conceptual explanation with visual aids"
        elif action_type == "visual":
            return "Visual demonstration without narration"
        else:
            return "Presenter speaking to camera"
    
    def _suggest_narration(self, transcript: str, ocr_text: str, action_type: str, visual_desc: str, seed_topic: str) -> str:
        """Suggest narration using transcript + OCR context."""
        # If there's already good transcript, use it
        if transcript and len(transcript) > 20:
            return transcript
        
        # Generate contextual narration based on action type + OCR
        templates = {
            "terminal": f"Here I run the commands to set up {seed_topic}.",
            "code": f"Let me show you the key code for {seed_topic}.",
            "browser": f"Now I'll navigate the {seed_topic} documentation.",
            "ide": f"Here in the IDE, I'm setting up {seed_topic}.",
            "terminal": f"Running the commands to initialize {seed_topic}.",
            "ui": f"Now I'll configure the {seed_topic} settings.",
            "demo": f"Here's a live demo of {seed_topic} in action.",
            "explanation": f"This is how {seed_topic} works under the hood.",
            "visual": f"Watch as {seed_topic} processes the data.",
            "talking": f"Let me explain this part of {seed_topic}."
        }
        return templates.get(action_type, f"Let me explain this part of {seed_topic}.")
    
    def _generate_script_structure(self, segments: List[Dict], seed_topic: str) -> Dict[str, Any]:
        """
        Build the final script structure mapping scenes to video segments.
        """
        scenes = []
        
        # Intro scene
        scenes.append({
            "index": 0,
            "type": "intro",
            "duration": 5.0,
            "visual_description": f"Hook: {seed_topic} - compelling opening visual",
            "narration": f"What if I told you {seed_topic} could change everything? Let me show you.",
            "source": "generated"
        })
        
        scene_idx = 1
        for seg in segments:
            if seg["role"] == "asset":
                # User's screen recording segment - use as-is
                scenes.append({
                    "index": scene_idx,
                    "type": "asset",
                    "duration": seg["duration"],
                    "start_time": seg["start"],
                    "end_time": seg["end"],
                    "visual_description": seg["visual_description"],
                    "narration": seg["suggested_narration"],
                    "source": "user_recording",
                    "asset_segment": {
                        "start": seg["start"],
                        "end": seg["end"],
                        "action_type": seg["action_type"]
                    }
                })
                scene_idx += 1
                
                # Check if next segment needs a bridge
                # (simplified - in reality would look ahead)
            elif seg["role"] == "bridge" and seg["duration"] > 3:
                # Generated bridge content
                scenes.append({
                    "index": scene_idx,
                    "type": "bridge",
                    "duration": min(seg["duration"], 10.0),
                    "visual_description": seg["visual_description"],
                    "narration": seg["suggested_narration"],
                    "source": "generated"
                })
                scene_idx += 1
        
        # Outro scene
        scenes.append({
            "index": scene_idx,
            "type": "outro",
            "duration": 5.0,
            "visual_description": f"CTA: Subscribe for more {seed_topic} content",
            "narration": f"If this helped you understand {seed_topic}, hit subscribe. Links in description.",
            "source": "generated"
        })
        
        total_asset_duration = sum(s["duration"] for s in scenes if s["type"] == "asset")
        total_generated_duration = sum(s["duration"] for s in scenes if s["type"] in ("intro", "outro", "bridge"))
        
        return {
            "seed_topic": seed_topic,
            "total_scenes": len(scenes),
            "total_asset_duration": total_asset_duration,
            "total_generated_duration": total_generated_duration,
            "estimated_total_duration": total_asset_duration + total_generated_duration,
            "scenes": scenes
        }


# Convenience function for direct use
def analyze_video(video_path: str, seed_topic: str = "", run_dir: str = None, config: Dict = None) -> Dict:
    """Quick analysis function."""
    agent = VideoAnalysisAgent(config)
    return agent.analyze(video_path, seed_topic, run_dir)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python video_analysis_agent.py <video_path> [seed_topic] [run_dir]")
        sys.exit(1)
    
    video_path = sys.argv[1]
    seed_topic = sys.argv[2] if len(sys.argv) > 2 else ""
    run_dir = sys.argv[3] if len(sys.argv) > 3 else None
    
    result = analyze_video(video_path, seed_topic, run_dir)
    print(json.dumps(result, indent=2, default=str))