import os
import json
import time
import datetime
import threading
from croniter import croniter
from pipeline.orchestrator import PipelineOrchestrator

class BackgroundScheduler:
    def __init__(self, workspace_dir=None, orchestrator=None):
        if workspace_dir is None:
            # Auto-detect: scheduler.py is in pipeline/, workspace is one level up
            workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.workspace_dir = workspace_dir
        self.config_path = os.path.join(self.workspace_dir, "config", "config.json")
        self.orchestrator = orchestrator or PipelineOrchestrator(workspace_dir=self.workspace_dir)
        self.thread = None
        self.running = False

    def load_scheduler_config(self):
        if not os.path.exists(self.config_path):
            return {"enabled": False}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                return config.get("scheduler", {"enabled": False})
        except Exception:
            return {"enabled": False}

    def save_next_run(self, next_run_dt):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            if "scheduler" not in config:
                config["scheduler"] = {}
            config["scheduler"]["next_run"] = next_run_dt.isoformat()
            tmp_path = self.config_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp_path, self.config_path)
        except Exception as e:
            print(f"[Scheduler Error] Could not save next run: {str(e)}")

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print("[Scheduler] Started background cron scheduler thread.")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
            print("[Scheduler] Stopped background scheduler.")

    def _run_loop(self):
        while self.running:
            try:
                sched_config = self.load_scheduler_config()
                if not sched_config.get("enabled", False):
                    time.sleep(10)
                    continue

                cron_expr = sched_config.get("cron")
                if not cron_expr:
                    time.sleep(10)
                    continue

                now = datetime.datetime.now()
                next_run_str = sched_config.get("next_run")
                
                # Check if we need to calculate/re-calculate
                calculate_next = False
                next_run = None
                if not next_run_str:
                    calculate_next = True
                else:
                    try:
                        next_run = datetime.datetime.fromisoformat(next_run_str)
                    except Exception:
                        calculate_next = True

                # If cron expression changed or not set, calculate from now
                if calculate_next or not next_run:
                    base = datetime.datetime.now()
                    iter = croniter(cron_expr, base)
                    next_run = iter.get_next(datetime.datetime)
                    self.save_next_run(next_run)
                    print(f"[Scheduler] Calculated next scheduled run: {next_run.isoformat()}")

                # Check if it is time to run
                if now >= next_run:
                    print(f"[Scheduler] Cron trigger activated! Launching scheduled pipeline run...")
                    
                    seed = sched_config.get("seed", "AI Automation")
                    audience = sched_config.get("audience", "General")
                    competitors = sched_config.get("competitors", "")
                    
                    run_id, _ = self.orchestrator.create_new_run(
                        topic_seed=seed,
                        target_audience=audience,
                        competitor_analysis=competitors
                    )
                    
                    def run_pipeline():
                        try:
                            self.orchestrator.execute(run_id)
                        except Exception as e:
                            print(f"[Scheduler] Scheduled run failed: {str(e)}")

                    threading.Thread(target=run_pipeline, daemon=True).start()

                    # Recalculate next run from the current moment
                    base = datetime.datetime.now()
                    iter = croniter(cron_expr, base)
                    new_next_run = iter.get_next(datetime.datetime)
                    self.save_next_run(new_next_run)
                    print(f"[Scheduler] Scheduled pipeline run started (run_id: {run_id}). Next run: {new_next_run.isoformat()}")

            except Exception as e:
                print(f"[Scheduler Error] Exception in loop: {str(e)}")
            
            # Check every 10 seconds
            time.sleep(10)
