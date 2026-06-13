import os
import time
import requests
import hashlib
import hmac
import base64
from datetime import datetime
from youtube_factory.providers.base import VideoProvider

class KlingProvider(VideoProvider):
    def __init__(self, config):
        super().__init__(config)
        self.credits_limit = 66
        self._usage_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "workspace", ".kling_daily_usage"
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
        api_key = self.config.get("api_key", "")
        api_secret = self.config.get("api_secret", "")
        return bool(api_key and api_secret and len(api_key) > 10)

    def get_priority(self) -> int:
        return 2

    def _generate_token(self):
        api_key = self.config.get("api_key", "")
        api_secret = self.config.get("api_secret", "")

        now = datetime.utcnow()
        exp = int(now.timestamp()) + 1800
        nbf = int(now.timestamp()) - 5

        header = base64.urlsafe_b64encode(
            '{"alg":"HS256","typ":"JWT"}'.encode()
        ).rstrip(b"=").decode()

        payload_data = {
            "iss": api_key,
            "exp": exp,
            "iat": int(now.timestamp()),
            "nbf": nbf
        }
        import json
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_data).encode()
        ).rstrip(b"=").decode()

        signing_input = f"{header}.{payload}"
        signature = hmac.new(
            api_secret.encode(),
            signing_input.encode(),
            hashlib.sha256
        ).digest()
        sig = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()

        return f"{header}.{payload}.{sig}"

    def generate(self, prompt, output_path, duration=5.0, aspect_ratio="16:9"):
        if self.credits_remaining <= 0:
            print("[KlingProvider] No credits remaining")
            return False

        try:
            base_url = self.config.get("base_url", "https://api.klingai.com")
            token = self._generate_token()

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            model = self.config.get("model", "kling-v2")
            mode = "std" if duration <= 5 else "pro"
            credits_per_gen = 20 if mode == "pro" else 5

            if self.credits_used + credits_per_gen > self.credits_limit:
                mode = "std"
                credits_per_gen = 5
                if self.credits_used + credits_per_gen > self.credits_limit:
                    print("[KlingProvider] Daily credit limit reached")
                    return False

            aspect = "16:9" if aspect_ratio == "16:9" else "9:16"

            payload = {
                "model_name": model,
                "prompt": prompt,
                "negative_prompt": "ugly, blurry, low quality, distorted",
                "cfg_scale": 0.5,
                "mode": mode,
                "aspect_ratio": aspect,
                "duration": "5" if duration <= 5 else "10"
            }

            submit_url = f"{base_url}/v1/videos/text2video"
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=30)

            if resp.status_code != 200 and resp.status_code != 201:
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("message", resp.text[:200])
                    error_code = error_data.get("code", 0)
                except Exception:
                    error_msg = resp.text[:200]
                    error_code = 0
                
                if error_code == 1003:
                    print("[KlingProvider] ERROR: API plan not activated!")
                    print("[KlingProvider] Please go to https://app.klingai.com/global/dev/")
                    print("[KlingProvider] and subscribe to an API plan.")
                elif error_code == 1000:
                    print("[KlingProvider] ERROR: Authentication failed - check API keys")
                else:
                    print(f"[KlingProvider] API error {error_code}: {error_msg}")
                return False

            data = resp.json()
            task_id = data.get("data", {}).get("task_id")
            if not task_id:
                print(f"[KlingProvider] No task_id in response: {data}")
                return False

            poll_url = f"{base_url}/v1/videos/text2video/{task_id}"
            max_polls = 60
            poll_interval = 5

            print(f"[KlingProvider] Task {task_id} submitted, polling...")
            for i in range(max_polls):
                time.sleep(poll_interval)
                status_resp = requests.get(poll_url, headers=headers, timeout=15)

                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    task_status = status_data.get("data", {}).get("task_status")

                    if task_status == "succeed":
                        videos = status_data.get("data", {}).get("task_result", {}).get("videos", [])
                        if videos:
                            video_url = videos[0].get("url")
                            if video_url:
                                video_resp = requests.get(video_url, timeout=120)
                                if video_resp.status_code == 200:
                                    with open(output_path, "wb") as f:
                                        f.write(video_resp.content)
                                    self.credits_used += credits_per_gen
                                    self._save_usage()
                                    print(f"[KlingProvider] Video saved to {output_path}")
                                    return True
                        return False
                    elif task_status == "failed":
                        print("[KlingProvider] Generation failed")
                        return False

            print("[KlingProvider] Timeout waiting for generation")
            return False
        except Exception as e:
            print(f"[KlingProvider] Generation failed: {e}")
            return False
