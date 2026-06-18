import os
import json
import time
from datetime import datetime
from youtube_factory.providers.base import VideoProvider
from youtube_factory.providers.veo import VeoProvider
from youtube_factory.providers.kling import KlingProvider


class VideoProviderManager:
    def __init__(self, config):
        self.config = config
        self.providers = []
        self._init_providers()
        self._usage_log = {}
        self._log_file = None

    def _init_providers(self):
        provider_configs = [
            ("veo", VeoProvider, 1, self.config.get("video_providers", {}).get("veo", {})),
            ("kling", KlingProvider, 2, self.config.get("video_providers", {}).get("kling", {})),
        ]

        for name, provider_class, priority, provider_config in provider_configs:
            try:
                merged_config = {**self.config, **provider_config}
                provider = provider_class(merged_config)
                if provider.is_available():
                    self.providers.append(provider)
                    print(f"[VideoProviderManager] Registered provider: {name} (priority {priority})")
            except Exception as e:
                print(f"[VideoProviderManager] Failed to init {name}: {e}")

        self.providers.sort(key=lambda p: p.get_priority())

    def _init_logging(self, run_dir):
        try:
            log_path = os.path.join(run_dir, "video_providers.log")
            self._log_file = open(log_path, "a", encoding="utf-8")
            self._log(f"=== VideoProviderManager started at {datetime.now().isoformat()} ===")
            self._log(f"Available providers: {[p.__class__.__name__ for p in self.providers]}")
        except Exception:
            self._log_file = None

    def _log(self, msg):
        if msg is None:
            msg = "None"
        else:
            msg = str(msg)
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        safe_msg = msg.encode("cp1252", errors="replace").decode("cp1252")
        print(f"[VideoProviderManager] {safe_msg}")
        if self._log_file:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except Exception:
                pass

    def _close_logging(self):
        if self._log_file:
            try:
                self._log_file.write(f"=== VideoProviderManager ended at {datetime.now().isoformat()} ===\n")
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def generate_video(self, prompt, output_path, duration=5.0, aspect_ratio="16:9", scene_type="generic"):
        for provider in self.providers:
            try:
                self._log(f"Trying {provider.__class__.__name__} for scene type '{scene_type}'")
                start_time = time.time()

                success = provider.generate(
                    prompt=prompt,
                    output_path=output_path,
                    duration=duration,
                    aspect_ratio=aspect_ratio
                )

                elapsed = time.time() - start_time

                if success and os.path.exists(output_path):
                    file_size = os.path.getsize(output_path)
                    self._log(f"SUCCESS: {provider.__class__.__name__} generated {file_size} bytes in {elapsed:.1f}s")
                    self._track_usage(provider.__class__.__name__, scene_type, elapsed)
                    return True
                else:
                    self._log(f"FAILED: {provider.__class__.__name__} returned False")

            except Exception as e:
                self._log(f"ERROR: {provider.__class__.__name__} exception: {type(e).__name__}: {e}")
                continue

        self._log("FAILED: All providers exhausted")
        return False

    def get_scene_provider(self, scene_type):
        for provider in self.providers:
            if provider.credits_remaining > 0:
                return provider
        return None

    def _track_usage(self, provider_name, scene_type, generation_time):
        if provider_name not in self._usage_log:
            self._usage_log[provider_name] = {
                "count": 0,
                "total_time": 0,
                "scene_types": {}
            }

        self._usage_log[provider_name]["count"] += 1
        self._usage_log[provider_name]["total_time"] += generation_time

        scene_types = self._usage_log[provider_name]["scene_types"]
        if scene_type not in scene_types:
            scene_types[scene_type] = 0
        scene_types[scene_type] += 1

    def get_usage_report(self):
        report = {
            "providers": {},
            "total_generated": 0,
            "total_time": 0
        }

        for provider in self.providers:
            name = provider.__class__.__name__
            usage = self._usage_log.get(name, {})
            report["providers"][name] = {
                "credits_used": provider.credits_used,
                "credits_limit": provider.credits_limit,
                "credits_remaining": provider.credits_remaining,
                "generations": usage.get("count", 0),
                "avg_time": usage.get("total_time", 0) / max(usage.get("count", 0), 1),
                "scene_types": usage.get("scene_types", {})
            }
            report["total_generated"] += usage.get("count", 0)
            report["total_time"] += usage.get("total_time", 0)

        return report

    def save_usage_report(self, run_dir):
        try:
            report = self.get_usage_report()
            report_path = os.path.join(run_dir, "video_provider_usage.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            self._log(f"Usage report saved to {report_path}")
        except Exception as e:
            self._log(f"Failed to save usage report: {e}")

    def generate_scene(self, scene, images_dir, run_dir):
        idx = scene["scene_index"]
        visual_desc = scene["visual_description"]
        scene_type = scene.get("scene_type", "generic")
        duration = scene.get("duration", 5.0)

        output_video_path = os.path.join(images_dir, f"scene_{idx}.mp4")
        output_image_path = os.path.join(images_dir, f"scene_{idx}.jpg")

        if os.path.exists(output_video_path):
            return {"path": output_video_path, "type": "video", "cached": True}
        if os.path.exists(output_image_path):
            return {"path": output_image_path, "type": "image", "cached": True}

        aspect_ratio = self.config.get("video_settings", {}).get("aspect_ratio", "16:9")

        prompt = visual_desc
        if scene.get("spoken_text"):
            prompt = f"{visual_desc}. Context: {scene['spoken_text'][:200]}"

        success = self.generate_video(
            prompt=prompt,
            output_path=output_video_path,
            duration=duration,
            aspect_ratio=aspect_ratio,
            scene_type=scene_type
        )

        if success:
            return {"path": output_video_path, "type": "video", "cached": False}
        else:
            return None
