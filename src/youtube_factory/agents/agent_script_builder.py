"""
ScriptBuilderAgent - Builds a complete pipeline-ready script from VideoAnalysis output.
Maps user's screen recording segments to scenes, generates missing narration,
and creates the full script structure for the video pipeline.
"""

import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from pipeline.llm_utils import query_llm as _query_llm


class ScriptBuilderAgent:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.max_narration_length = self.config.get("max_narration_length", 200)  # chars per scene
        self.intro_duration = self.config.get("intro_duration", 5.0)
        self.outro_duration = self.config.get("outro_duration", 5.0)
        self.max_gap_duration = self.config.get("max_gap_duration", 10.0)
        
    def build_script(self, video_analysis: Dict[str, Any], seed_topic: str = "") -> Dict[str, Any]:
        """
        Build complete script from video analysis.
        
        Input: video_analysis.json from VideoAnalysisAgent
        Output: {
            "script_text": "...",           # Full markdown script with [Narrator]/[Visual] pairs
            "voice_metadata": {...},        # Compatible with pipeline voiceover stage
            "visual_segments": [...],       # Compatible with pipeline visuals stage
            "scenes": [...],                # Detailed scene breakdown
            "total_duration": float,
            "asset_duration": float,
            "generated_duration": float
        }
        """
        segments = video_analysis.get("segments", [])
        topic = seed_topic or video_analysis.get("seed_topic", "Tech Video")
        video_duration = video_analysis.get("total_duration", 0)
        
        print(f"[ScriptBuilder] Building script for: {topic}")
        print(f"[ScriptBuilder] {len(segments)} asset segments, {video_duration:.1f}s total")
        
        # Group consecutive segments of same type
        grouped = self._group_segments(segments)
        print(f"[ScriptBuilder] Grouped into {len(grouped)} scene groups")
        
        # Build scenes: intro + asset groups + bridges + outro
        scenes = []
        
        # 1. Intro scene
        scenes.append(self._build_intro_scene(topic))
        
        # 2. Process each group
        for i, group in enumerate(grouped):
            # Add asset scenes for this group
            asset_scenes = self._build_asset_scenes(group, topic, i)
            scenes.extend(asset_scenes)
            
            # Add bridge if not last group and there's a gap
            if i < len(grouped) - 1:
                bridge = self._build_bridge(group, grouped[i + 1], topic)
                if bridge:
                    scenes.append(bridge)
        
        # 3. Outro scene
        scenes.append(self._build_outro_scene(topic))
        
        # Renumber all scenes sequentially (including intro, bridges, outro)
        for idx, scene in enumerate(scenes):
            scene["index"] = idx
            
        # Query LLM to write professional, continuous narration matching scene durations
        self._generate_narration_with_llm(scenes, topic)
        
        # Generate outputs
        script_text = self._render_script(scenes)
        voice_metadata = self._build_voice_metadata(scenes, video_duration)
        visual_segments = self._build_visual_segments(scenes, video_analysis)
        
        total_duration = sum(s["duration"] for s in scenes)
        asset_duration = sum(s["duration"] for s in scenes if s.get("source") == "user_recording")
        generated_duration = total_duration - asset_duration
        
        result = {
            "script_text": script_text,
            "voice_metadata": voice_metadata,
            "visual_segments": visual_segments,
            "scenes": scenes,
            "total_duration": total_duration,
            "asset_duration": asset_duration,
            "generated_duration": generated_duration,
            "scene_count": len(scenes),
            "generated_at": datetime.now().isoformat(),
            "seed_topic": topic
        }
        
        print(f"[ScriptBuilder] Built {len(scenes)} scenes, {total_duration:.1f}s total ({asset_duration:.1f}s asset + {generated_duration:.1f}s generated)")
        return result

    def _generate_narration_with_llm(self, scenes: List[Dict], topic: str) -> None:
        """Query the LLM to generate high-quality continuous narration matching scene durations."""
        print(f"[ScriptBuilder] Querying LLM for high-quality narration matching scene durations...")
        
        # Prepare scene descriptions for the prompt
        scene_list = []
        for s in scenes:
            idx = s["index"]
            dur = s["duration"]
            stype = s["type"]
            desc = s["visual_description"]
            
            # Extract OCR text if present
            ocr = ""
            if s.get("asset_segment") and s["asset_segment"].get("ocr_text"):
                ocr = s["asset_segment"]["ocr_text"]
                # Clean up multiple newlines/whitespace
                ocr = " ".join(ocr.split())
                # Truncate to keep prompt size reasonable
                ocr = ocr[:300]
                
            scene_info = f"Scene {idx} (Type: {stype}): Target Duration: {dur:.1f}s | Visual Description: {desc}"
            if ocr:
                scene_info += f" | OCR Text on screen: '{ocr}'"
            scene_list.append(scene_info)
            
        system_prompt = (
            "You are a professional video scriptwriter and educational content creator.\n"
            f"We are creating a high-quality video walkthrough about '{topic}'.\n"
            "We have a screen recording showing the demo, which has been divided into sequential scenes.\n"
            "Your goal is to write a cohesive, engaging, and professional voiceover narration for the video.\n\n"
            "CRITICAL RULES:\n"
            "1. Do NOT write a play-by-play narration (e.g., do NOT say 'now I am clicking here', 'first run this command', or 'next you see X'). "
            "The screen recording acts as the background/b-roll visual. Your narration should explain the underlying concepts, architecture, "
            "practical benefits, and flow of the topic naturally. Ground your explanation in the provided OCR text and topic.\n"
            "2. Word Count Constraints: The length of the narration for each scene MUST match the target duration of that scene. "
            "Assume a standard speaking speed of 2.4 words per second. For a scene of duration D, you MUST write approximately D * 2.4 words. "
            "For example: write ~140 words for a 60-second scene, ~36 words for a 15-second scene, etc. Failure to do this will result in the video being cut short!\n"
            "3. Grounding: Connect the narration to the technical details of the topic.\n"
            "4. OUTPUT FORMAT: Return ONLY a raw JSON object matching the schema below. Do NOT think out loud, do NOT explain your reasoning, do NOT count words manually in your output text, and do NOT write any markdown blocks (like ```json). Start your output directly with { and end with }."
        )
        
        user_prompt = (
            "Here is the chronological list of scenes for the video:\n\n"
            + "\n".join(scene_list) + "\n\n"
            "Generate the narration for each scene. Target word count for each scene = Duration * 2.4 words.\n"
            "Return ONLY the following JSON structure directly (do not wrap in markdown code blocks):\n"
            "{\n"
            "  \"scenes\": [\n"
            "    { \"index\": 0, \"narration\": \"Narration for scene 0...\" },\n"
            "    ...\n"
            "  ]\n"
            "}"
        )
        
        try:
            res = _query_llm(self.config, system_prompt, user_prompt, task="script", require_json=True)
            # Robust JSON block extraction using regex and brace matching
            json_str = res.strip()
            import re
            json_match = re.search(r"```json\s*(.*?)\s*```", json_str, re.DOTALL | re.IGNORECASE)
            if json_match:
                json_str = json_match.group(1).strip()
            else:
                json_match = re.search(r"```\s*(.*?)\s*```", json_str, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1).strip()
                else:
                    start = json_str.find("{")
                    end = json_str.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        json_str = json_str[start:end+1].strip()
                        
            data = json.loads(json_str)
            narrations = {int(item["index"]): item["narration"] for item in data.get("scenes", [])}
            
            # Overwrite scene narration if key is found
            for s in scenes:
                idx = s["index"]
                if idx in narrations and narrations[idx]:
                    s["narration"] = narrations[idx]
            print("[ScriptBuilder] Successfully generated and applied LLM narration for all scenes!")
        except Exception as e:
            import sys
            raw_res = locals().get('res', 'None')
            try:
                enc_res = raw_res.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
            except Exception:
                enc_res = "[Unencodable Response]"
            print(f"[ScriptBuilder] LLM narration query failed: {e}. Raw response: {enc_res}")
            import traceback
            traceback.print_exc()
            print(f"[ScriptBuilder] Falling back to programmatic templates.")
    
    def _group_segments(self, segments: List[Dict]) -> List[List[Dict]]:
        """
        Group consecutive segments with same action_type into logical units.
        """
        if not segments:
            return []
        
        groups = []
        current_group = [segments[0]]
        
        for seg in segments[1:]:
            # Group if same action_type and continuous
            if seg["action_type"] == current_group[-1]["action_type"]:
                current_group.append(seg)
            else:
                groups.append(current_group)
                current_group = [seg]
        
        groups.append(current_group)
        return groups
    
    def _build_intro_scene(self, topic: str) -> Dict:
        """Build hook/intro scene."""
        return {
            "index": 0,
            "type": "intro",
            "duration": self.intro_duration,
            "visual_description": f"Hook: {topic} - compelling opening visual with kinetic typography",
            "narration": f"What if I told you {topic} could change everything you know about AI automation? Let me show you.",
            "source": "generated",
            "asset_segment": None
        }
    
    def _build_asset_scenes(self, group: List[Dict], topic: str, group_idx: int) -> List[Dict]:
        """
        Split a group of segments into scenes (max ~15-20s each for good pacing).
        """
        scenes = []
        max_scene_duration = 18.0
        
        for seg in group:
            duration = seg["duration"]
            start = seg["start"]
            
            if duration <= max_scene_duration:
                # Single scene
                scenes.append(self._make_asset_scene(seg, topic, len(scenes)))
            else:
                # Split into multiple scenes
                num_scenes = int(duration / max_scene_duration) + 1
                scene_duration = duration / num_scenes
                
                for j in range(num_scenes):
                    scene_start = start + j * scene_duration
                    scene_end = min(start + (j + 1) * scene_duration, start + duration)
                    
                    scenes.append({
                        "index": len(scenes),
                        "type": "asset",
                        "duration": scene_end - scene_start,
                        "start_time": scene_start,
                        "end_time": scene_end,
                        "visual_description": f"{seg['visual_description']} (part {j+1}/{num_scenes})",
                        "narration": self._split_narration(seg.get("suggested_narration", ""), j, num_scenes, topic),
                        "source": "user_recording",
                        "asset_segment": {
                            "start": scene_start,
                            "end": scene_end,
                            "action_type": seg["action_type"],
                            "ocr_text": seg.get("ocr_text", "")
                        }
                    })
        
        return scenes
    
    def _make_asset_scene(self, seg: Dict, topic: str, scene_idx: int) -> Dict:
        """Create single asset scene from segment."""
        return {
            "index": scene_idx,
            "type": "asset",
            "duration": seg["duration"],
            "start_time": seg["start"],
            "end_time": seg["end"],
            "visual_description": seg["visual_description"],
            "narration": seg.get("suggested_narration", f"Watch this {seg['action_type']} segment."),
            "source": "user_recording",
            "asset_segment": {
                "start": seg["start"],
                "end": seg["end"],
                "action_type": seg["action_type"],
                "ocr_text": seg.get("ocr_text", "")
            }
        }
    
    def _split_narration(self, narration: str, part: int, total: int, topic: str) -> str:
        """Split narration across multiple scenes."""
        if not narration or total <= 1:
            return narration or f"Continuing with {topic}..."
        
        # Simple split by sentences
        sentences = [s.strip() for s in narration.split(".") if s.strip()]
        per_scene = max(1, len(sentences) // total)
        start = part * per_scene
        end = min(start + per_scene, len(sentences))
        
        if start < len(sentences):
            return ". ".join(sentences[start:end]) + "."
        return f"Continuing with {topic}..."
    
    def _build_bridge(self, current_group: List[Dict], next_group: List[Dict], topic: str) -> Optional[Dict]:
        """Build transition scene between different action types."""
        current_type = current_group[-1]["action_type"]
        next_type = next_group[0]["action_type"]
        
        if current_type == next_type:
            return None
        
        bridge_narrations = {
            ("terminal", "code"): f"Now let's look at the code behind what we just ran.",
            ("terminal", "browser"): f"Let's check the documentation for what we just configured.",
            ("terminal", "ui"): f"Now I'll show you the UI for what we just set up.",
            ("code", "terminal"): f"Let's run the code we just wrote.",
            ("code", "browser"): f"Let's reference the docs for this implementation.",
            ("browser", "terminal"): f"Now back to the terminal to apply what we learned.",
            ("ui", "terminal"): f"Let's run the commands for what we just configured.",
            ("ide", "terminal"): f"Now let's execute this in the terminal.",
        }
        
        narration = bridge_narrations.get(
            (current_type, next_type),
            f"Moving from {current_type} to {next_type} for {topic}."
        )
        
        return {
            "index": -1,  # Will be renumbered
            "type": "bridge",
            "duration": min(self.max_gap_duration, 8.0),
            "visual_description": f"Transition: {current_type} → {next_type}",
            "narration": narration,
            "source": "generated",
            "asset_segment": None
        }
    
    def _build_outro_scene(self, topic: str) -> Dict:
        """Build CTA/outro scene."""
        return {
            "index": -1,
            "type": "outro",
            "duration": self.outro_duration,
            "visual_description": f"CTA: Subscribe for more {topic} deep dives",
            "narration": f"If this helped you understand {topic}, hit subscribe and check the description for links. See you in the next one.",
            "source": "generated",
            "asset_segment": None
        }
    
    def _render_script(self, scenes: List[Dict]) -> str:
        """Render scenes to final markdown script format."""
        lines = []
        for i, scene in enumerate(scene for scene in scenes if scene.get("index", 0) >= 0):
            # Renumber
            scene["index"] = i
            
            # Check if next scene is bridge
            is_bridge = scene.get("type") == "bridge"
            
            lines.append(f"[Narrator]: {scene['narration']}")
            lines.append(f"[Visual: {scene['visual_description']}]")
            lines.append("")  # blank line between scenes
        
        return "\n".join(lines).strip()
    
    def _build_voice_metadata(self, scenes: List[Dict], video_duration: float) -> Dict:
        """Build voice_metadata.json compatible with pipeline."""
        voice_scenes = []
        for i, scene in enumerate(scenes):
            if scene.get("index", 0) >= 0:
                voice_scenes.append({
                    "scene_index": i,
                    "visual_description": scene["visual_description"],
                    "spoken_text": scene["narration"],
                    "duration": scene["duration"]
                })
        
        return {
            "scenes": voice_scenes,
            "total_duration": sum(s["duration"] for s in scenes)
        }
    
    def _build_visual_segments(self, scenes: List[Dict], video_analysis: Dict) -> List[Dict]:
        """Build visual segment mapping for pipeline."""
        video_path = video_analysis.get("video_path", "")
        segments = video_analysis.get("segments", [])
        
        visual_segments = []
        for scene in scenes:
            if scene.get("type") == "asset" and scene.get("asset_segment"):
                asset_seg = scene["asset_segment"]
                visual_segments.append({
                    "scene_index": scene["index"],
                    "type": "explicit_asset",
                    "description": scene["visual_description"],
                    "duration": scene["duration"],
                    "asset_tag": f"[Visual: asset:video:{video_path}]",
                    "asset_params": {
                        "source": video_path,
                        "start": asset_seg.get("start", 0),
                        "end": asset_seg.get("end", asset_seg.get("start", 0) + scene["duration"])
                    }
                })
            elif scene.get("type") in ("intro", "outro", "bridge"):
                visual_segments.append({
                    "scene_index": scene["index"],
                    "type": "generated",
                    "description": scene["visual_description"],
                    "duration": scene["duration"],
                    "asset_tag": None,
                    "asset_params": None
                })
        
        return visual_segments


def build_script_from_analysis(video_analysis_path: str, output_path: str = None, seed_topic: str = "") -> Dict:
    """Convenience function."""
    with open(video_analysis_path, "r", encoding="utf-8") as f:
        video_analysis = json.load(f)
    
    if not seed_topic:
        seed_topic = video_analysis.get("seed_topic", "Tech Video")
    
    agent = ScriptBuilderAgent()
    result = agent.build_script(video_analysis, seed_topic)
    
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"[ScriptBuilder] Saved to: {output_path}")
    
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python script_builder_agent.py <video_analysis.json> [output.json] [seed_topic]")
        sys.exit(1)
    
    analysis_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    topic = sys.argv[3] if len(sys.argv) > 3 else ""
    
    result = build_script_from_analysis(analysis_path, output_path, topic)
    print(f"\nScript built: {result['scene_count']} scenes, {result['total_duration']:.1f}s total")