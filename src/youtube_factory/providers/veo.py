import os
import time
import requests
import base64
from youtube_factory.providers.base import VideoProvider

class VeoProvider(VideoProvider):
    def __init__(self, config):
        super().__init__(config)
        self.credits_limit = 12
        self._usage_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "workspace", ".veo_daily_usage"
        )
        self._load_usage()

    def _load_usage(self):
        try:
            if os.path.exists(self._usage_file):
                with open(self._usage_file, "r") as f:
                    data = f.read().strip().split("|")
                    if len(data) == 2:
                        date_str, count = data
                        from datetime import date
                        if date_str == date.today().isoformat():
                            self.credits_used = int(count)
        except Exception:
            pass

    def _save_usage(self):
        try:
            os.makedirs(os.path.dirname(self._usage_file), exist_ok=True)
            from datetime import date
            with open(self._usage_file, "w") as f:
                f.write(f"{date.today().isoformat()}|{self.credits_used}")
        except Exception:
            pass

    def is_available(self) -> bool:
        gemini_config = self.config.get("gemini", {})
        api_key = gemini_config.get("api_key", "")
        return bool(api_key and api_key != "YOUR_GEMINI_API_KEY" and len(api_key) > 20)

    def get_priority(self) -> int:
        return 1

    def generate(self, prompt, output_path, duration=5.0, aspect_ratio="16:9"):
        if self.credits_remaining <= 0:
            print("[VeoProvider] No credits remaining")
            return False

        try:
            gemini_config = self.config.get("gemini", {})
            api_key = gemini_config.get("api_key")
            model = self.config.get("veo", {}).get("model", "veo-3.1-generate-preview")

            # Step 1: Submit long-running prediction
            submit_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning?key={api_key}"

            payload = {
                "instances": [
                    {
                        "prompt": f"A {duration} second video: {prompt}"
                    }
                ],
                "parameters": {
                    "sampleCount": 1,
                    "aspectRatio": aspect_ratio
                }
            }

            print(f"[VeoProvider] Submitting video generation task...")
            resp = requests.post(submit_url, json=payload, timeout=60)

            if resp.status_code != 200:
                try:
                    error_data = resp.json()
                    error_code = error_data.get("error", {}).get("code", 0)
                    error_msg = error_data.get("error", {}).get("message", "")[:200]
                except Exception:
                    error_code = resp.status_code
                    error_msg = resp.text[:200]

                if error_code == 429:
                    print("[VeoProvider] Rate limit exceeded - quota exhausted")
                    print("[VeoProvider] Wait for quota reset or check billing at https://ai.dev/rate-limit")
                else:
                    print(f"[VeoProvider] Submit failed: {error_code} - {error_msg}")
                return False

            data = resp.json()
            operation_name = data.get("name")
            if not operation_name:
                print(f"[VeoProvider] No operation name in response: {data}")
                return False

            print(f"[VeoProvider] Operation: {operation_name}")

            # Step 2: Poll for completion
            check_url = f"https://generativelanguage.googleapis.com/v1beta/{operation_name}?key={api_key}"
            max_polls = 120
            poll_interval = 10

            print(f"[VeoProvider] Polling for completion...")
            for i in range(max_polls):
                time.sleep(poll_interval)
                check_resp = requests.get(check_url, timeout=30)

                if check_resp.status_code == 200:
                    check_data = check_resp.json()

                    if check_data.get("done"):
                        # Step 3: Extract video from response
                        response = check_data.get("response", {})
                        predictions = response.get("predictions", [])

                        for pred in predictions:
                            bytesContainer = pred.get("bytesInlineData", {})
                            if bytesContainer.get("mimeType", "").startswith("video/"):
                                video_b64 = bytesContainer.get("data", "")
                                if video_b64:
                                    video_data = base64.b64decode(video_b64)
                                    with open(output_path, "wb") as f:
                                        f.write(video_data)
                                    self.credits_used += 1
                                    self._save_usage()
                                    print(f"[VeoProvider] Video saved to {output_path} ({len(video_data)} bytes)")
                                    return True

                        print(f"[VeoProvider] Task completed but no video found")
                        return False
                    else:
                        if i % 6 == 0:
                            print(f"[VeoProvider] Still processing... (poll {i+1}/{max_polls})")
                else:
                    print(f"[VeoProvider] Poll error: {check_resp.status_code}")

            print(f"[VeoProvider] Timeout after {max_polls * poll_interval}s")
            return False

        except Exception as e:
            print(f"[VeoProvider] Generation failed: {e}")
            return False
