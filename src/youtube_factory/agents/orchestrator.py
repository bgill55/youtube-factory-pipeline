import os
import json
import datetime
import traceback
import sys
import threading

from .config_loader import load_config

class PipelineHaltError(Exception):
    """Raised when a critical pipeline stage fails and the pipeline should halt."""
    pass

# Define pipeline stages
STAGE_RESEARCH = "RESEARCH"
STAGE_SCRAPE = "SCRAPE"
STAGE_IDEA = "IDEA_GEN"
STAGE_SCRIPT = "SCRIPTWRITE"
STAGE_GUIDE = "GUIDE_GEN"
STAGE_VOICE = "VOICEOVER"
STAGE_VISUAL = "VISUALS"
STAGE_AUDIO_GEN = "AUDIO_GEN"
STAGE_ASSEMBLY = "ASSEMBLY"
STAGE_SHORTS = "SHORTS"
STAGE_GUIDE_DEPLOY = "GUIDE_DEPLOY"
STAGE_UPLOAD = "UPLOAD"
STAGE_COMPLETED = "COMPLETED"

# Asset-First pipeline stages
STAGE_VIDEO_ANALYSIS = "VIDEO_ANALYSIS"
STAGE_SCRIPT_BUILD = "SCRIPT_BUILD"

STAGES = [
    STAGE_RESEARCH,
    STAGE_SCRAPE,
    STAGE_IDEA,
    STAGE_SCRIPT,
    STAGE_GUIDE,
    STAGE_VOICE,
    STAGE_VISUAL,
    STAGE_AUDIO_GEN,
    STAGE_ASSEMBLY,
    STAGE_GUIDE_DEPLOY,
    STAGE_SHORTS,
    STAGE_UPLOAD
]

# Asset-First pipeline (bypasses research/idea/script/guide)
ASSET_FIRST_STAGES = [
    STAGE_VIDEO_ANALYSIS,
    STAGE_SCRIPT_BUILD,
    STAGE_GUIDE,
    STAGE_VOICE,
    STAGE_VISUAL,
    STAGE_AUDIO_GEN,
    STAGE_ASSEMBLY,
    STAGE_SHORTS,
    STAGE_GUIDE_DEPLOY,
    STAGE_UPLOAD
]

class PipelineOrchestrator:
    def __init__(self, workspace_dir=None):
        if workspace_dir is None:
            # Auto-detect workspace directory (parent of pipeline directory)
            pipeline_dir = os.path.dirname(os.path.abspath(__file__))
            workspace_dir = os.path.dirname(pipeline_dir)
        self.workspace_dir = workspace_dir
        self.config_path = os.path.join(self.workspace_dir, "config", "config.json")
        self.runs_dir = os.path.join(self.workspace_dir, "workspace", "runs")
        self.config = self.load_config()
        
        # Ensure directories exist
        os.makedirs(self.runs_dir, exist_ok=True)

    def load_config(self):
        return load_config(config_path=self.config_path)

    def create_new_run(self, topic_seed, target_audience="", competitor_analysis=""):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"run_{timestamp}"
        run_dir = os.path.join(self.runs_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)

        state = {
            "run_id": run_id,
            "run_dir": run_dir,
            "current_step": STAGE_RESEARCH,
            "topic_seed": topic_seed,
            "target_audience": target_audience,
            "competitor_analysis": competitor_analysis,
            "steps": {stage: {"status": "PENDING", "output": None, "updated_at": None} for stage in STAGES}
        }
        self.save_state(run_dir, state)
        return run_id, state

    _state_lock = threading.RLock()
    _cancelled_runs = set()

    @classmethod
    def cancel_run(cls, run_id):
        cls._cancelled_runs.add(run_id)

    @classmethod
    def is_cancelled(cls, run_id):
        return run_id in cls._cancelled_runs

    @classmethod
    def clear_cancel(cls, run_id):
        cls._cancelled_runs.discard(run_id)

    def _read_state_file(self, run_dir):
        state_path = os.path.join(run_dir, "run_state.json")
        if not os.path.exists(state_path):
            raise FileNotFoundError(f"No run state found at {state_path}")
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            # State file corrupted — try the .tmp backup
            tmp_path = state_path + ".tmp"
            if os.path.exists(tmp_path):
                print(f"[Orchestrator] State file corrupted, recovering from .tmp backup")
                with open(tmp_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            raise

    def _write_state_file(self, run_dir, state):
        state_path = os.path.join(run_dir, "run_state.json")
        tmp_path = state_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
        except Exception as e:
            # If writing to tmp fails, try direct write
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            return

        # Try atomic replacement with retries, fallback to direct write
        import time
        for attempt in range(10):
            try:
                os.replace(tmp_path, state_path)
                return
            except PermissionError:
                time.sleep(0.1)
        
        # Final fallback: direct write to the file
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            raise PermissionError(f"Failed to write state file even with fallback: {e}")


    def load_state(self, run_dir_or_id):
        if os.path.isabs(run_dir_or_id):
            run_dir = run_dir_or_id
        else:
            run_dir = os.path.join(self.runs_dir, run_dir_or_id)
            
        with PipelineOrchestrator._state_lock:
            return self._read_state_file(run_dir)

    def save_state(self, run_dir, state):
        with PipelineOrchestrator._state_lock:
            self._write_state_file(run_dir, state)

    def update_run_state(self, run_dir_or_id, updates):
        """Thread-safe way to load state, apply updates (shallow dict merge), and save it."""
        if os.path.isabs(run_dir_or_id):
            run_dir = run_dir_or_id
        else:
            run_dir = os.path.join(self.runs_dir, run_dir_or_id)
            
        with PipelineOrchestrator._state_lock:
            try:
                state = self._read_state_file(run_dir)
            except FileNotFoundError:
                state = {}
                
            for k, v in updates.items():
                if isinstance(v, dict) and k in state and isinstance(state[k], dict):
                    state[k].update(v)
                else:
                    state[k] = v
            self._write_state_file(run_dir, state)
            return state

    def update_step_status(self, run_dir, stage, status):
        """Thread-safe update of a single step's status in run_state.json."""
        with PipelineOrchestrator._state_lock:
            state = self._read_state_file(run_dir)
            if "steps" not in state:
                state["steps"] = {}
            if stage not in state["steps"]:
                state["steps"][stage] = {}
            state["steps"][stage]["status"] = status
            state["steps"][stage]["updated_at"] = datetime.datetime.now().isoformat()
            self._write_state_file(run_dir, state)
            return state

    def update_step_success(self, run_dir, stage, output, next_step):
        """Thread-safe update of a step to SUCCESS and advancing current_step."""
        with PipelineOrchestrator._state_lock:
            state = self._read_state_file(run_dir)
            if "steps" not in state:
                state["steps"] = {}
            if stage not in state["steps"]:
                state["steps"][stage] = {}
            state["steps"][stage]["status"] = "SUCCESS"
            state["steps"][stage]["output"] = output
            state["steps"][stage]["updated_at"] = datetime.datetime.now().isoformat()
            state["current_step"] = next_step
            self._write_state_file(run_dir, state)
            return state

    def update_step_failure(self, run_dir, stage, error_msg, next_step=None, halt=False):
        """Thread-safe update of a step to FAILED, optionally advancing current_step."""
        with PipelineOrchestrator._state_lock:
            state = self._read_state_file(run_dir)
            if "steps" not in state:
                state["steps"] = {}
            if stage not in state["steps"]:
                state["steps"][stage] = {}
            state["steps"][stage]["status"] = "FAILED"
            state["steps"][stage]["error"] = error_msg
            state["steps"][stage]["updated_at"] = datetime.datetime.now().isoformat()
            if not halt and next_step:
                state["current_step"] = next_step
            self._write_state_file(run_dir, state)
            return state

    def log_to_run(self, run_dir, message):
        log_path = os.path.join(run_dir, "run.log")
        timestamp = datetime.datetime.now().isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
        try:
            print(f"[{timestamp}] {message}")
        except Exception:
            pass

    def execute(self, run_id_or_dir):
        # Normalize run path so callers can pass either a bare run_id or an absolute run dir
        if os.path.isabs(run_id_or_dir):
            run_dir = run_id_or_dir
        else:
            # Support both legacy IDs and run directory paths ending in STAGE_COMPLETED
            if run_id_or_dir.startswith("run_"):
                run_dir = os.path.join(self.runs_dir, run_id_or_dir)
            else:
                # Fallback: treat the input as a directory path directly
                run_dir = run_id_or_dir

        state = self.load_state(run_dir)
        run_id = state.get("run_id", os.path.basename(run_dir))

        # Backward compatibility: inject SHORTS into steps if missing (added after ASSEMBLY)
        for stage in STAGES:
            if stage not in state["steps"]:
                state["steps"][stage] = {"status": "PENDING", "output": None, "updated_at": None}

        self.log_to_run(run_dir, f"Starting YouTube Factory Pipeline execution for run: {state['run_id']}")

        # Lazy import of agent modules to handle dependency issues cleanly
        from .agent_idea import IdeaGeneratorAgent
        from .agent_research import ResearchAgent
        from .agent_script import ScriptwriterAgent
        from .agent_guide import GuideGeneratorAgent
        from .agent_voice import VoiceoverAgent
        from .agent_visual import VisualAssetAgent
        from .agent_audio_gen import AudioGeneratorAgent
        from .agent_video import VideoAssemblerAgent
        from .short_generator import ShortGenerator
        from .agent_uploader import UploaderAgent
        from .agent_scraper import WebsiteScraper
        from .agent_video_analysis import VideoAnalysisAgent
        from .agent_script_builder import ScriptBuilderAgent

        agents = {
            STAGE_RESEARCH: ResearchAgent(self.config),
            STAGE_SCRAPE: WebsiteScraper(self.config),
            STAGE_IDEA: IdeaGeneratorAgent(self.config),
            STAGE_SCRIPT: ScriptwriterAgent(self.config),
            STAGE_GUIDE: GuideGeneratorAgent(self.config),
            STAGE_VOICE: VoiceoverAgent(self.config),
            STAGE_VISUAL: VisualAssetAgent(self.config),
            STAGE_AUDIO_GEN: AudioGeneratorAgent(self.config),
            STAGE_ASSEMBLY: VideoAssemblerAgent(self.config),
            STAGE_GUIDE_DEPLOY: GuideGeneratorAgent(self.config),
            STAGE_SHORTS: ShortGenerator(self.config),
            STAGE_UPLOAD: UploaderAgent(self.config),
            STAGE_VIDEO_ANALYSIS: VideoAnalysisAgent(self.config),
            STAGE_SCRIPT_BUILD: ScriptBuilderAgent(self.config)
        }

        state = self.load_state(run_dir)

        # Detect Asset-First run mode: check for uploaded screen recording
        assets_dir = os.path.join(run_dir, "assets")
        asset_files = []
        if os.path.exists(assets_dir):
            asset_files = [f for f in os.listdir(assets_dir) 
                          if f.lower().endswith((".mp4", ".mov", ".webm", ".mkv", ".avi"))]
        
        is_asset_first_run = len(asset_files) > 0
        
        if is_asset_first_run:
            self.log_to_run(run_dir, f"Asset-First mode detected: {len(asset_files)} screen recording(s) uploaded")
            # Use the first uploaded video
            asset_video = asset_files[0]
            asset_video_path = os.path.join(assets_dir, asset_video)
            # Update state with asset info
            state["asset_first"] = True
            state["asset_video"] = asset_video
            state["asset_video_path"] = asset_video_path
            state["topic_seed"] = state.get("topic_seed", "Screen Recording Walkthrough")
            
            # If we are starting at the very beginning, advance current_step to VIDEO_ANALYSIS
            if state.get("current_step") == STAGE_RESEARCH:
                state["current_step"] = STAGE_VIDEO_ANALYSIS
            
            # Ensure all asset-first stages are in steps dictionary
            if "steps" not in state:
                state["steps"] = {}
            for stage in ASSET_FIRST_STAGES:
                if stage not in state["steps"]:
                    state["steps"][stage] = {"status": "PENDING", "output": None, "updated_at": None}
            
            # Save the updated state to disk so the run loop picks it up
            self.save_state(run_dir, state)
            
            # Switch to Asset-First stages
            pipeline_stages = ASSET_FIRST_STAGES
            self.log_to_run(run_dir, f"Using Asset-First pipeline with video: {asset_video}")
        else:
            pipeline_stages = STAGES

        # Always load workspace scraped content as fallback so source material is available
        workspace_scraped_path = os.path.join(self.workspace_dir, "workspace", "scraped_content.json")
        try:
                with open(workspace_scraped_path, "r", encoding="utf-8") as f:
                    workspace_scraped = json.load(f)
                # Inject into state only if not already set or empty
                existing_scrape = state.get("steps", {}).get(STAGE_SCRAPE, {}).get("output")
                if not existing_scrape or (
                    isinstance(existing_scrape, dict) and not existing_scrape.get("user_notes") and not (existing_scrape.get("pages") or [])
                ):
                    state["steps"][STAGE_SCRAPE] = {
                        "status": "SUCCESS",
                        "output": workspace_scraped,
                        "updated_at": datetime.datetime.now().isoformat(),
                    }
                    self.log_to_run(run_dir, "Injected workspace scraped_content.json into run state")
        except Exception as e:
            self.log_to_run(run_dir, f"Failed to load workspace scraped content: {e}")

        # If topic_seed is blank, check for pre-scraped content in workspace
        # This allows using scraped content directly without RESEARCH/SCRAPE stages
        topic_seed = (state.get("topic_seed", "") or "").strip()
        if not topic_seed or topic_seed.lower() == "[scraped]":
            # Only perform initial injection and step reset if we are at the very beginning
            if state["current_step"] in [STAGE_RESEARCH, STAGE_SCRAPE]:
                if os.path.exists(workspace_scraped_path):
                    self.log_to_run(run_dir, f"Blank topic_seed — loading pre-scraped content from {workspace_scraped_path}")
                    try:
                        with open(workspace_scraped_path, "r", encoding="utf-8") as f:
                            scraped_data = json.load(f)
                        # Inject scraped content into state so IDEA_GEN can use it
                        state["steps"][STAGE_SCRAPE] = {"status": "SUCCESS", "output": scraped_data, "updated_at": datetime.datetime.now().isoformat()}
                        state["steps"][STAGE_RESEARCH] = {"status": "SUCCESS", "output": {"skipped": True, "reason": "Using pre-scraped content"}, "updated_at": datetime.datetime.now().isoformat()}
                        # Update current step to IDEA_GEN AND mark seed as [scraped] for IDEA_GEN agent
                        state["current_step"] = STAGE_IDEA
                        state["topic_seed"] = "[scraped]"
                        self.save_state(run_dir, state)
                    except Exception as e:
                        self.log_to_run(run_dir, f"Failed to load scraped content: {e}")

        try:
            while True:
                # Check if run was cancelled
                if self.is_cancelled(run_id):
                    self.log_to_run(run_dir, "Pipeline CANCELLED by user.")
                    self.update_run_state(run_dir, {"current_step": "CANCELLED"})
                    break

                # Reload state from disk to get latest current_step and inputs
                state = self.load_state(run_dir)
                current_stage = state["current_step"]
                
                # Handle Asset-First completion
                if current_stage == STAGE_COMPLETED:
                    break
                
                if current_stage == STAGE_COMPLETED:
                    break

                self.log_to_run(run_dir, f"--- Executing Stage: {current_stage} ---")
                
                # Atomically update step status to RUNNING
                state = self.update_step_status(run_dir, current_stage, "RUNNING")

                import time
                max_stage_retries = self.config.get("pipeline", {}).get("stage_retries", 2)
                stage_attempts = 0
                stage_success = False

                while stage_attempts <= max_stage_retries and not stage_success:
                    try:
                        # Gather inputs for current stage
                        agent = agents[current_stage]

                        # Fetch inputs from state or previous stages
                        inputs = {}
                        if current_stage == STAGE_RESEARCH:
                            inputs = {
                                "topic_seed": state["topic_seed"],
                                "workspace_dir": self.workspace_dir,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_SCRAPE:
                            inputs = {
                                "url": self.config.get("scraper", {}).get("url", ""),
                                "workspace_dir": self.workspace_dir,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_IDEA:
                            inputs = {
                                "topic_seed": state["topic_seed"],
                                "target_audience": state["target_audience"],
                                "competitor_analysis": state["competitor_analysis"],
                                "research_context": state["steps"][STAGE_RESEARCH]["output"],
                                "scraped_content": state["steps"][STAGE_SCRAPE]["output"] if state["steps"].get(STAGE_SCRAPE) and state["steps"][STAGE_SCRAPE].get("output") else None,
                                "workspace_dir": self.workspace_dir,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_SCRIPT:
                            inputs = {
                                "idea_output": state["steps"][STAGE_IDEA]["output"],
                                "target_audience": state.get("target_audience", ""),
                                "scraped_content": state["steps"][STAGE_SCRAPE]["output"] if state["steps"].get(STAGE_SCRAPE) and state["steps"][STAGE_SCRAPE].get("output") else None,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_GUIDE:
                            idea_output = state["steps"].get(STAGE_IDEA, {}).get("output") or {} if state["steps"].get(STAGE_IDEA) else {}
                            script_output = state["steps"].get(STAGE_SCRIPT, {}).get("output") or {} if state["steps"].get(STAGE_SCRIPT) else {}
                            research_output = state["steps"].get(STAGE_RESEARCH, {}).get("output") or {} if state["steps"].get(STAGE_RESEARCH) else {}
                            
                            if state.get("asset_first"):
                                script_build_out = state["steps"].get(STAGE_SCRIPT_BUILD, {}).get("output") or {}
                                if isinstance(script_build_out, dict):
                                    topic = script_build_out.get("seed_topic", "Screen Recording")
                                    idea_output = {
                                        "selected_topic": topic,
                                        "concept_summary": f"Step-by-step educational guide for {topic}.",
                                        "keywords": [topic.lower()],
                                        "video_goal": f"Show how to use {topic}"
                                    }
                                    script_output = {
                                        "script_content": script_build_out.get("script_text", "")
                                    }
                            
                            inputs = {
                                "idea_output": idea_output,
                                "script_output": script_output,
                                "research_output": research_output,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_VIDEO_ANALYSIS:
                            # Analyze uploaded screen recording
                            asset_video_path = state.get("asset_video_path")
                            if not asset_video_path or not os.path.exists(asset_video_path):
                                raise PipelineHaltError("Asset video not found for VIDEO_ANALYSIS")
                            # VideoAnalysisAgent uses .analyze() not .run()
                            agent = agents[STAGE_VIDEO_ANALYSIS]
                            output = agent.analyze(
                                video_path=asset_video_path,
                                seed_topic=state.get("topic_seed", "Screen Recording"),
                                run_dir=run_dir
                            )
                            # Skip normal agent.run() flow - mark success and move to next
                            stage_success = True
                            self.update_step_success(run_dir, STAGE_VIDEO_ANALYSIS, output, STAGE_SCRIPT_BUILD)
                            continue
                        elif current_stage == STAGE_SCRIPT_BUILD:
                            # Build script from video analysis using ScriptBuilderAgent
                            agent = agents[STAGE_SCRIPT_BUILD]
                            video_analysis_path = os.path.join(run_dir, "video_analysis.json")
                            if not os.path.exists(video_analysis_path):
                                raise PipelineHaltError("Video analysis not found for SCRIPT_BUILD")
                            with open(video_analysis_path, "r", encoding="utf-8") as f:
                                video_analysis = json.load(f)
                            script_result = self._build_script_from_analysis(video_analysis, run_dir)
                            output = script_result
                            stage_success = True
                            self.update_step_success(run_dir, STAGE_SCRIPT_BUILD, output, STAGE_VOICE)
                            continue
                        elif current_stage == STAGE_VOICE:
                            # In asset-first mode, script comes from SCRIPT_BUILD not SCRIPT
                            script_key = STAGE_SCRIPT_BUILD if state.get("asset_first") else STAGE_SCRIPT
                            inputs = {
                                "script_output": state["steps"][script_key]["output"],
                                "run_dir": run_dir
                            }

                        elif current_stage == STAGE_VISUAL:
                            # In asset-first mode, script comes from SCRIPT_BUILD not SCRIPT
                            script_key = STAGE_SCRIPT_BUILD if state.get("asset_first") else STAGE_SCRIPT
                            # For idea_output, in asset-first mode we don't have IDEA_GEN, use empty
                            idea_output = state["steps"].get(STAGE_IDEA, {}).get("output") or {}
                            inputs = {
                                "script_output": state["steps"][script_key]["output"],
                                "idea_output": idea_output,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_AUDIO_GEN:
                            # In asset-first mode, we don't have IDEA_GEN - use idea from SCRIPT_BUILD
                            idea_output = state["steps"].get(STAGE_IDEA, {}).get("output") or {}
                            if state.get("asset_first"):
                                script_output = state["steps"].get(STAGE_SCRIPT_BUILD, {}).get("output", {})
                                if isinstance(script_output, dict) and "scenes" in script_output:
                                    # Extract concept from first scene
                                    idea_output = {"concept_summary": script_output.get("seed_topic", "Screen Recording")}
                            inputs = {
                                "audio_duration": state["steps"][STAGE_VOICE]["output"].get("duration"),
                                "idea_output": idea_output,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_ASSEMBLY:
                            inputs = {
                                "audio_file": state["steps"][STAGE_VOICE]["output"].get("audio_file"),
                                "audio_duration": state["steps"][STAGE_VOICE]["output"].get("duration"),
                                "visuals_output": state["steps"][STAGE_VISUAL]["output"],
                                "bg_music_file": state["steps"][STAGE_AUDIO_GEN]["output"].get("bg_music_file") if state["steps"].get(STAGE_AUDIO_GEN) and state["steps"][STAGE_AUDIO_GEN].get("output") else None,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_SHORTS:
                            inputs = {
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_GUIDE_DEPLOY:
                            # In asset-first mode, get idea from script_build output
                            idea_output = state["steps"].get(STAGE_IDEA, {}).get("output") or {} if state["steps"].get(STAGE_IDEA) else {}
                            if state.get("asset_first"):
                                script_output = state["steps"].get(STAGE_SCRIPT_BUILD, {}).get("output")
                                if isinstance(script_output, dict):
                                    topic = script_output.get("seed_topic", "Screen Recording")
                                    idea_output = {
                                        "selected_topic": topic,
                                        "concept_summary": f"In-depth walkthrough and demonstration of {topic}.",
                                        "keywords": [topic.lower()]
                                    }
                            
                            guide_output = state["steps"].get(STAGE_GUIDE, {}).get("output")
                            if state.get("asset_first") and not guide_output:
                                # In asset-first, generate guide from script
                                guide_output = state["steps"].get(STAGE_SCRIPT_BUILD, {}).get("output")
                            
                            inputs = {
                                "guide_output": guide_output,
                                "idea_output": idea_output,
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_UPLOAD:
                            idea_output = state["steps"].get(STAGE_IDEA, {}).get("output") or {} if state["steps"].get(STAGE_IDEA) else {}
                            if state.get("asset_first"):
                                script_output = state["steps"].get(STAGE_SCRIPT_BUILD, {}).get("output")
                                if isinstance(script_output, dict):
                                    topic = script_output.get("seed_topic", "Screen Recording")
                                    idea_output = {
                                        "selected_topic": topic,
                                        "concept_summary": f"In-depth walkthrough and demonstration of {topic}.",
                                        "keywords": [topic.lower()]
                                    }
                            inputs = {
                                "video_file": state["steps"][STAGE_ASSEMBLY]["output"].get("video_file"),
                                "visuals_output": state["steps"][STAGE_VISUAL]["output"],
                                "idea_output": idea_output,
                                "guide_output": state["steps"].get(STAGE_GUIDE_DEPLOY, {}).get("output"),
                                "short_output": state["steps"].get(STAGE_SHORTS, {}).get("output"),
                                "run_dir": run_dir
                            }
                        # Skip SHORTS if disabled in config
                        if current_stage == STAGE_SHORTS and not self.config.get("upload_settings", {}).get("generate_shorts", True):
                            output = {"status": "SKIPPED", "reason": "generate_shorts is disabled in config"}
                        elif current_stage == STAGE_UPLOAD and self.is_cancelled(run_id):
                            output = {"status": "SKIPPED", "reason": "Pipeline was cancelled — upload blocked"}
                        else:
                            # Validate critical inputs exist before running stage
                            validation_errors = []
                            if current_stage == STAGE_VOICE and not inputs.get("script_output"):
                                validation_errors.append("Script output missing — SCRIPTWRITE stage may have failed")
                            if current_stage == STAGE_VISUAL and not inputs.get("script_output"):
                                validation_errors.append("Script output missing — SCRIPTWRITE stage may have failed")
                            if current_stage == STAGE_ASSEMBLY:
                                if not inputs.get("audio_file"):
                                    validation_errors.append("Audio file missing — VOICEOVER stage may have failed")
                                if not inputs.get("visuals_output"):
                                    validation_errors.append("Visuals output missing — VISUALS stage may have failed")
                            if current_stage == STAGE_UPLOAD:
                                if not inputs.get("video_file"):
                                    validation_errors.append("Video file missing — ASSEMBLY stage may have failed")
                                if not inputs.get("idea_output"):
                                    validation_errors.append("Idea output missing — IDEA_GEN stage may have failed")

                            if validation_errors:
                                for err in validation_errors:
                                    self.log_to_run(run_dir, f"VALIDATION ERROR: {err}")
                                raise PipelineHaltError(f"Stage {current_stage} cannot run: {'; '.join(validation_errors)}")

                        # Manage services for stages that need them
                        if current_stage == STAGE_VISUAL:
                            image_provider = self.config.get("image_provider", "pollinations")
                            
                            # Skip SD WebUI check if using static backgrounds
                            if image_provider not in ("local_sd", "pexels_stock", "fal_video"):
                                self.log_to_run(run_dir, f"Image provider '{image_provider}' — no SD WebUI needed")
                            else:
                                from services.service_manager import get_service_manager
                                svc_mgr = get_service_manager(self.config)
                                if svc_mgr:
                                    sd_ok, sd_msg = svc_mgr.can_start_service("stable_diffusion")
                                    if sd_ok:
                                        self.log_to_run(run_dir, f"Stable Diffusion: {sd_msg}")
                                        if not svc_mgr.require_service("stable_diffusion", run_dir):
                                            self.log_to_run(run_dir, "SD failed to start, will use Pollinations fallback")
                                    else:
                                        self.log_to_run(run_dir, f"Stable Diffusion unavailable: {sd_msg} — using Pollinations fallback")

                        if current_stage == STAGE_VOICE:
                            voice_provider = self.config.get("voice_provider", "gemini")
                            
                            # Skip service checks for providers that don't need local services
                            if voice_provider == "edge-tts":
                                self.log_to_run(run_dir, "Voice provider is edge-tts — no OmniVoice needed")
                            elif voice_provider == "supertonic_http":
                                self.log_to_run(run_dir, "Voice provider is Supertonic (supertonic_http) — no OmniVoice needed")
                            else:
                                from services.service_manager import get_service_manager
                                svc_mgr = get_service_manager(self.config)
                                if svc_mgr:
                                    self.log_to_run(run_dir, "Checking required services for VOICEOVER stage...")
                                    omnivoice_ok, omnivoice_msg = svc_mgr.can_start_service("omnivoice")
                                    if omnivoice_ok:
                                        self.log_to_run(run_dir, f"OmniVoice: {omnivoice_msg}")
                                        if not svc_mgr.require_service("omnivoice", run_dir):
                                            self.log_to_run(run_dir, "OmniVoice failed to start, will use Edge TTS fallback")
                                    else:
                                        self.log_to_run(run_dir, f"OmniVoice unavailable: {omnivoice_msg} — using Edge TTS fallback")

                        if current_stage == STAGE_SHORTS:
                            output = agent.generate(run_dir, orchestrator=self)
                        elif current_stage == STAGE_GUIDE_DEPLOY:
                            output = agent.deploy(inputs)
                        elif current_stage == STAGE_SCRAPE:
                            # Check if saved scraped data exists from dashboard scrape
                            shared_path = os.path.join(self.workspace_dir, "workspace", "scraped_content.json")
                            has_saved_data = os.path.exists(shared_path)
                            has_config_url = bool(self.config.get("scraper", {}).get("url", ""))
                            
                            if not has_saved_data and not has_config_url:
                                output = {"status": "SKIPPED", "reason": "no scraper URL configured and no saved data", "pages": [], "total_pages": 0}
                            else:
                                output = agent.run(inputs)
                        else:
                            # Run Agent
                            output = agent.run(inputs)

                        # Move to next stage
                        if current_stage == STAGE_SCRIPT_BUILD:
                            next_step = STAGE_VOICE
                        else:
                            current_idx = pipeline_stages.index(current_stage)
                            if current_idx + 1 < len(pipeline_stages):
                                next_step = pipeline_stages[current_idx + 1]
                            else:
                                next_step = STAGE_COMPLETED

                        self.update_step_success(run_dir, current_stage, output, next_step)
                        self.log_to_run(run_dir, f"Stage {current_stage} finished successfully.")
                        stage_success = True

                    except Exception as e:
                        stage_attempts += 1
                        self.log_to_run(run_dir, f"ERROR in Stage {current_stage} (attempt {stage_attempts}/{max_stage_retries + 1}): {str(e)}")
                        
                        if stage_attempts <= max_stage_retries:
                            wait = 2 ** (stage_attempts - 1)
                            self.log_to_run(run_dir, f"Retrying {current_stage} in {wait}s...")
                            time.sleep(wait)
                            continue
                        
                        self.log_to_run(run_dir, traceback.format_exc())
                        
                        # Check if this stage is critical (halt pipeline) or non-critical (continue)
                        critical_stages = {"RESEARCH", "IDEA_GEN", "SCRIPTWRITE", "VOICEOVER", "ASSEMBLY", "UPLOAD"}
                        if current_stage in critical_stages:
                            self.update_step_failure(run_dir, current_stage, str(e), halt=True)
                            self.log_to_run(run_dir, f"Stage {current_stage} is critical — halting pipeline.")
                            raise PipelineHaltError(f"Critical stage {current_stage} failed: {e}")
                        else:
                            # Move to next stage
                            current_idx = STAGES.index(current_stage)
                            if current_idx + 1 < len(STAGES):
                                next_step = STAGES[current_idx + 1]
                            else:
                                next_step = STAGE_COMPLETED
                            self.update_step_failure(run_dir, current_stage, str(e), next_step=next_step, halt=False)
                            self.log_to_run(run_dir, f"Stage {current_stage} is non-critical — continuing pipeline.")
                        break

        except PipelineHaltError:
            # Re-raise to be caught by caller (app.py runs in thread)
            raise
        except Exception as e:
            self.log_to_run(run_dir, f"UNEXPECTED ERROR: {str(e)}")
            self.log_to_run(run_dir, traceback.format_exc())
            raise

        self.log_to_run(run_dir, "YouTube Factory Pipeline completed successfully!")
        return state

    def _build_script_from_analysis(self, video_analysis: dict, run_dir: str) -> dict:
        """Build script from video analysis using ScriptBuilderAgent."""
        from .agent_script_builder import ScriptBuilderAgent
        
        agent = ScriptBuilderAgent(self.config)
        result = agent.build_script(video_analysis, video_analysis.get("seed_topic", "Screen Recording"))
        
        # Save script output for pipeline compatibility
        script_output = result.get("voice_metadata", {})
        script_text = result.get("script_text", "")
        
        # Save script markdown
        import json
        script_path = os.path.join(run_dir, "script_asset_first.md")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_text)
        
        script_meta = {
            "script_file": script_path,
            "script_text": script_text,
            "voice_metadata": result.get("voice_metadata"),
            "visual_segments": result.get("visual_segments"),
            "scenes": result.get("scenes"),
            "total_duration": result.get("total_duration"),
            "asset_duration": result.get("asset_duration"),
            "generated_duration": result.get("generated_duration"),
            "seed_topic": result.get("seed_topic")
        }
        
        # Save script metadata
        meta_path = os.path.join(run_dir, "script_metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(script_meta, f, indent=2, ensure_ascii=False)
        
        return script_meta
