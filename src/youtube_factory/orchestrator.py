import os
import json
import datetime
import traceback
import sys
import threading

from youtube_factory.config import load_config

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
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, state_path)

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
        if os.path.isabs(run_id_or_dir):
            run_dir = run_id_or_dir
        else:
            run_dir = os.path.join(self.runs_dir, run_id_or_dir)

        state = self.load_state(run_dir)
        run_id = state.get("run_id", os.path.basename(run_dir))

        # Backward compatibility: inject SHORTS into steps if missing (added after ASSEMBLY)
        for stage in STAGES:
            if stage not in state["steps"]:
                state["steps"][stage] = {"status": "PENDING", "output": None, "updated_at": None}

        self.log_to_run(run_dir, f"Starting YouTube Factory Pipeline execution for run: {state['run_id']}")

        # Lazy import of agent modules to handle dependency issues cleanly
        from youtube_factory.agents.idea import IdeaGeneratorAgent
        from youtube_factory.agents.research import ResearchAgent
        from youtube_factory.agents.script import ScriptwriterAgent
        from youtube_factory.agents.guide import GuideGeneratorAgent
        from youtube_factory.agents.voice import VoiceoverAgent
        from youtube_factory.agents.visuals import VisualAssetAgent
        from youtube_factory.agents.audio import AudioGeneratorAgent
        from youtube_factory.agents.assembly import VideoAssemblerAgent
        from youtube_factory.shorts import ShortGenerator
        from youtube_factory.agents.uploader import UploaderAgent
        from youtube_factory.agents.scraper import WebsiteScraper

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
            STAGE_UPLOAD: UploaderAgent(self.config)
        }

        state = self.load_state(run_dir)

        # If topic_seed is blank, check for pre-scraped content in workspace
        # This allows using scraped content directly without RESEARCH/SCRAPE stages
        topic_seed = (state.get("topic_seed", "") or "").strip()
        if not topic_seed or topic_seed.lower() == "[scraped]":
            scraped_path = os.path.join(self.workspace_dir, "workspace", "scraped_content.json")
            if os.path.exists(scraped_path):
                self.log_to_run(run_dir, f"Blank topic_seed — loading pre-scraped content from {scraped_path}")
                try:
                    with open(scraped_path, "r", encoding="utf-8") as f:
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
                            inputs = {
                                "idea_output": state["steps"][STAGE_IDEA]["output"],
                                "script_output": state["steps"][STAGE_SCRIPT]["output"],
                                "research_output": state["steps"][STAGE_RESEARCH]["output"],
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_VOICE:
                            inputs = {
                                "script_output": state["steps"][STAGE_SCRIPT]["output"],
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_VISUAL:
                            inputs = {
                                "script_output": state["steps"][STAGE_SCRIPT]["output"],
                                "idea_output": state["steps"][STAGE_IDEA]["output"],
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_AUDIO_GEN:
                            inputs = {
                                "audio_duration": state["steps"][STAGE_VOICE]["output"].get("duration"),
                                "idea_output": state["steps"][STAGE_IDEA]["output"],
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
                            inputs = {
                                "guide_output": state["steps"][STAGE_GUIDE]["output"] if state["steps"].get(STAGE_GUIDE) and state["steps"][STAGE_GUIDE].get("output") else None,
                                "idea_output": state["steps"][STAGE_IDEA]["output"],
                                "run_dir": run_dir
                            }
                        elif current_stage == STAGE_UPLOAD:
                            inputs = {
                                "video_file": state["steps"][STAGE_ASSEMBLY]["output"].get("video_file"),
                                "visuals_output": state["steps"][STAGE_VISUAL]["output"],
                                "idea_output": state["steps"][STAGE_IDEA]["output"],
                                "guide_output": state["steps"][STAGE_GUIDE_DEPLOY]["output"] if state["steps"].get(STAGE_GUIDE_DEPLOY) and state["steps"][STAGE_GUIDE_DEPLOY].get("output") else None,
                                "short_output": state["steps"][STAGE_SHORTS]["output"] if state["steps"].get(STAGE_SHORTS) and state["steps"][STAGE_SHORTS].get("output") else None,
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
                            
                            # Skip OmniVoice check if using Edge TTS directly
                            if voice_provider == "edge-tts":
                                self.log_to_run(run_dir, "Voice provider is edge-tts — no OmniVoice needed")
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
                        current_idx = STAGES.index(current_stage)
                        if current_idx + 1 < len(STAGES):
                            next_step = STAGES[current_idx + 1]
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
