import os
import json
import requests
import urllib.parse
import re
import glob
import shutil
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from youtube_factory.llm import query_llm as _query_llm
from youtube_factory.prompts import get_system_prompt
from youtube_factory.video_providers import VideoProviderManager

class VisualAssetAgent:
    def __init__(self, config):
        self.config = config
        self._log_file = None
        self._video_manager = VideoProviderManager(config)

    def _init_logging(self, run_dir):
        """Initialize file logging for this run."""
        try:
            log_path = os.path.join(run_dir, "visual_agent.log")
            self._log_file = open(log_path, "a", encoding="utf-8")
            self._log(f"=== Visual Agent started at {datetime.now().isoformat()} ===")
        except Exception:
            self._log_file = None

    def _log(self, msg):
        """Write to both stdout and log file."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        # Sanitize for cp1252 console encoding
        safe_msg = msg.encode("cp1252", errors="replace").decode("cp1252")
        print(f"[Visual Agent] {safe_msg}")
        if self._log_file:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except Exception:
                pass

    def _close_logging(self):
        if self._log_file:
            try:
                self._log_file.write(f"=== Visual Agent ended at {datetime.now().isoformat()} ===\n")
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def _get_broll_library_path(self):
        """Get the path to the shared B-roll library."""
        workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(workspace, "workspace", "broll_library")

    def _load_broll_index(self):
        """Load the B-roll keyword index."""
        lib_path = self._get_broll_library_path()
        index_path = os.path.join(lib_path, "broll_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_broll_index(self, index):
        """Save the B-roll keyword index."""
        lib_path = self._get_broll_library_path()
        os.makedirs(lib_path, exist_ok=True)
        index_path = os.path.join(lib_path, "broll_index.json")
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    def _is_sd_available(self) -> bool:
        """Check if local Stable Diffusion WebUI is reachable. Caches result for 30s."""
        import time
        if hasattr(self, '_sd_health_cache') and (time.time() - self._sd_health_cache['time']) < 30:
            return self._sd_health_cache['available']
        
        local_sd_config = self.config.get("local_sd", {})
        base_url = local_sd_config.get("base_url", "http://127.0.0.1:7860")
        
        import socket
        from urllib.parse import urlparse
        is_sd_active = False
        try:
            parsed = urlparse(base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 7860
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex((host, port))
            if result == 0:
                is_sd_active = True
            sock.close()
        except Exception:
            pass
        
        self._sd_health_cache = {'time': time.time(), 'available': is_sd_active}
        return is_sd_active

    def _search_local_broll(self, keywords):
        """Search local B-roll library for a matching clip by keyword overlap."""
        index = self._load_broll_index()
        lib_path = self._get_broll_library_path()
        if not index or not os.path.exists(lib_path):
            return None
        # Split by both comma AND space to get individual words
        kw_set = set()
        for part in keywords.split(","):
            for word in part.lower().split():
                word = word.strip()
                if word:
                    kw_set.add(word)
        best_match = None
        best_score = 0
        for keyword, files in index.items():
            for f in files:
                if os.path.exists(f):
                    index_words = set(keyword.lower().split())
                    overlap = len(kw_set & index_words)
                    # Require at least 2 word overlap OR 50%+ of clip keywords matched
                    clip_len = len(index_words)
                    if overlap >= 2 or (clip_len <= 3 and overlap >= clip_len):
                        score = overlap / max(clip_len, 1)
                        if score > best_score:
                            best_score = score
                            best_match = f
        if best_match and best_score >= 0.4:
            return best_match
        return None

    def _save_to_broll_library(self, source_path, keywords):
        """Save a downloaded clip to the shared B-roll library for future reuse."""
        try:
            lib_path = self._get_broll_library_path()
            os.makedirs(lib_path, exist_ok=True)
            # Generate a deterministic filename from keywords
            safe_kw = "_".join(k.lower().strip()[:20] for k in keywords.split(",") if k.strip())[:60]
            import hashlib
            hash_suffix = hashlib.md5(source_path.encode()).hexdigest()[:8]
            dest_filename = f"{safe_kw}_{hash_suffix}.mp4"
            dest_path = os.path.join(lib_path, dest_filename)
            if not os.path.exists(dest_path):
                shutil.copy2(source_path, dest_path)
                # Update index
                index = self._load_broll_index()
                for k in keywords.split(","):
                    k = k.strip().lower()
                    if k:
                        if k not in index:
                            index[k] = []
                        if dest_path not in index[k]:
                            index[k].append(dest_path)
                self._save_broll_index(index)
                self._log(f"B-Roll Library: Saved {dest_filename} tagged with '{keywords}'")
        except Exception as e:
            self._log(f"B-Roll Library: Save failed (non-critical): {e}")

    def query_llm_for_pexels_query(self, visual_description):
        """Extracts multiple keyword variations for stock video search (Pexels)."""
        system_prompt = get_system_prompt("visual_keywords", visual_description=visual_description)
        user_prompt = "Generate 3 keyword variations now."
        default_kws = ["technology", "computer", "digital"]

        try:
            res = _query_llm(self.config, system_prompt, user_prompt, task="visual_keywords").strip()
            # Try to parse JSON response
            import re
            # Extract JSON from response
            json_match = re.search(r'\{[^}]+\}', res)
            if json_match:
                data = json.loads(json_match.group())
                kws = data.get("keywords", [])
                if kws and len(kws) >= 1:
                    # Clean each keyword variation
                    cleaned = []
                    for kw in kws[:3]:
                        kw = kw.strip().replace('"', '').replace("'", "")
                        kw = re.sub(r'[^a-zA-Z0-9\s\-]', '', kw).strip()
                        words = kw.split()[:3]
                        cleaned.append(" ".join(words))
                    return cleaned
            # Fallback: try to extract from plain text
            if "\n" in res:
                lines = [l.strip() for l in res.strip().split("\n") if l.strip()]
                last_line = lines[-1]
                kws = [k.strip().strip('"').strip("'") for k in last_line.split(",")]
                cleaned = []
                for kw in kws[:3]:
                    kw = re.sub(r'[^a-zA-Z0-9\s\-]', '', kw).strip()
                    words = kw.split()[:3]
                    if words:
                        cleaned.append(" ".join(words))
                if cleaned:
                    return cleaned
        except Exception as e:
            print(f"[Visual Agent] LLM keyword extraction failed: {e}")

        # Fallback: extract words from description
        words = [w.lower() for w in visual_description.split() if len(w) > 3 and w.isalpha()]
        if len(words) >= 2:
            return [" ".join(words[:2]), " ".join(words[2:4]) if len(words) > 3 else words[0]]
        return default_kws

    def download_pexels_video(self, keywords, output_path, aspect_ratio="16:9", target_duration=None):
        api_key = self.config.get("pexels_api_key")
        if not api_key or api_key.startswith("${") or api_key == "YOUR_PEXELS_API_KEY" or api_key.strip() == "":
            raise ValueError("Pexels API Key is not configured. Set PEXELS_API_KEY in .env (get one from https://www.pexels.com/api/).")
            
        orientation = "landscape" if aspect_ratio == "16:9" else "portrait"
        url = f"https://api.pexels.com/videos/search?query={requests.utils.quote(keywords)}&orientation={orientation}&per_page=15&page=1"
        headers = {
            "Authorization": api_key
        }
        
        print(f"[Visual Agent] Searching Pexels stock video for '{keywords}'...")
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise Exception(f"Pexels API returned status {response.status_code}: {response.text[:200]}")
            
        data = response.json()
        videos = data.get("videos", [])
        if not videos:
            raise ValueError(f"No stock videos found on Pexels for query '{keywords}'")
            
        # Score and rank videos by quality and relevance
        scored = []
        for video in videos:
            video_files = video.get("video_files", [])
            duration = video.get("duration", 0)
            best_file = None
            best_score = -1
            for f in video_files:
                if f.get("file_type") != "video/mp4":
                    continue
                w = f.get("width") or 0
                h = f.get("height") or 0
                is_correct_orientation = (orientation == "landscape" and w > h) or (orientation == "portrait" and h > w)
                if not is_correct_orientation:
                    continue
                # Minimum resolution filter
                if w < 1280:
                    continue
                score = 0
                if f.get("quality") == "hd":
                    score += 10
                if w >= 1920:
                    score += 5
                # Duration scoring — prefer clips within 20% of target
                if target_duration and duration > 0:
                    ratio = duration / target_duration
                    if 0.8 <= ratio <= 1.2:
                        score += 8
                    elif 0.5 <= ratio <= 1.5:
                        score += 4
                if score > best_score:
                    best_score = score
                    best_file = f
            if best_file:
                scored.append((best_score, best_file, duration))
                
        if not scored:
            # Fallback: accept any mp4
            for video in videos:
                for f in video.get("video_files", []):
                    if f.get("file_type") == "video/mp4":
                        scored.append((0, f, video.get("duration", 0)))
                        break
                if scored:
                    break
                    
        if not scored:
            raise ValueError(f"No suitable mp4 link found for videos matching '{keywords}'")
            
        # Pick best scored video (with slight randomization among top 3)
        scored.sort(key=lambda x: x[0], reverse=True)
        top_n = scored[:3]
        import random
        best_link = random.choice(top_n)[1]["link"]
                
        print(f"[Visual Agent] Downloading Pexels video from {best_link} to {output_path}...")
        self.download_file(best_link, output_path)
        print(f"[Visual Agent] Downloaded Pexels video saved successfully to {output_path}")

    def generate_image_fal_video(self, prompt, output_path, aspect_ratio="16:9"):
        api_key = self.config.get("fal_api_key")
        if not api_key:
            raise ValueError("Fal.ai API Key is not configured.")
            
        model_name = self.config.get("fal_video_model", "fal-ai/hunyuan-video")
        video_size = "landscape_16_9" if aspect_ratio == "16:9" else "portrait_9_16"
        
        headers = {
            "Authorization": f"Key {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "prompt": prompt,
            "video_size": video_size
        }
        
        submit_url = f"https://queue.fal.run/{model_name}"
        print(f"[Visual Agent] Submitting to Fal.ai queue ({model_name}) with prompt: '{prompt[:100]}...'")
        
        resp = requests.post(submit_url, json=payload, headers=headers, timeout=30)
        if resp.status_code not in (200, 201):
            raise Exception(f"Fal.ai submit failed: {resp.status_code} - {resp.text}")
            
        res_data = resp.json()
        request_id = res_data.get("request_id")
        if not request_id:
            video_url = res_data.get("video", {}).get("url")
            if video_url:
                self.download_file(video_url, output_path)
                return
            raise Exception(f"Failed to submit to Fal.ai queue, no request_id: {res_data}")
            
        status_url = f"https://queue.fal.run/{model_name}/requests/{request_id}/status"
        result_url = f"https://queue.fal.run/{model_name}/requests/{request_id}"
        
        max_polls = 120
        poll_interval = 5.0
        
        print(f"[Visual Agent] Polling Fal.ai task {request_id}...")
        for i in range(max_polls):
            status_resp = requests.get(status_url, headers=headers, timeout=15)
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                status = status_data.get("status")
                if status == "COMPLETED":
                    res_resp = requests.get(result_url, headers=headers, timeout=15)
                    if res_resp.status_code == 200:
                        res_data = res_resp.json()
                        video_url = res_data.get("video", {}).get("url")
                        if video_url:
                            print(f"[Visual Agent] Downloading video from Fal.ai: {video_url}...")
                            self.download_file(video_url, output_path)
                            return
                        raise Exception(f"No video URL in completed Fal.ai result: {res_data}")
                    raise Exception(f"Failed to fetch completed Fal.ai result: {res_resp.text}")
                elif status == "FAILED":
                    logs = status_data.get("logs", "")
                    raise Exception(f"Fal.ai generation failed: {logs}")
                else:
                    queue_position = status_data.get("queue_position", 0)
                    print(f"[Visual Agent] Fal.ai status: {status} (Queue position: {queue_position}). Retrying in {poll_interval}s...")
            else:
                print(f"[Visual Agent] Warning: Status poll returned {status_resp.status_code}. Retrying...")
                
            time.sleep(poll_interval)
            
        raise TimeoutError("Fal.ai generation timed out.")

    def download_file(self, url, output_path):
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    def query_llm_for_prompt(self, visual_description):
        """Converts a raw visual description into a detailed image generation prompt."""
        system_prompt = get_system_prompt("visual_prompt", visual_description=visual_description)
        user_prompt = "Generate the image prompt now."

        try:
            prompt = _query_llm(self.config, system_prompt, user_prompt, task="visual_prompt")
            if prompt:
                return prompt.strip()
        except Exception as e:
            print(f"[Visual Agent] query_llm_for_prompt failed: {e}")

        return f"A highly detailed, professional 3D render of: {visual_description}. Realistic materials, clean studio lighting, high contrast, vibrant colors, dark clean background, 3D tech asset."

    def check_if_scene_needs_screenshot(self, visual_description, spoken_text, featured_links):
        """
        Queries the configured Text LLM to classify if the scene describes visiting a website or showing a web page.
        If yes, matches and returns the matching URL from the featured_links list.
        """
        if not featured_links:
            return None

        # Direct matching bypass: if URL is explicitly written in visual description or spoken text
        for link in featured_links:
            url = link.get("url")
            if url:
                # Remove trailing slashes and common prefixes for flexible matching
                clean_url = url.lower().rstrip("/").replace("https://", "").replace("http://", "").replace("www.", "")
                clean_desc = visual_description.lower()
                clean_spoken = spoken_text.lower()
                if clean_url in clean_desc or clean_url in clean_spoken:
                    print(f"[Visual Agent] Direct URL match found for {url} in visual/spoken text. Bypassing LLM check.")
                    return url

        # Fuzzy/regex matching bypass: check if the link name, domain name, or repository name is mentioned
        generic_domains = {"github.com", "huggingface.co", "gitlab.com", "bitbucket.org", "google.com", "youtube.com"}
        clean_desc = visual_description.lower()
        clean_spoken = spoken_text.lower()

        for link in featured_links:
            url = link.get("url")
            if not url:
                continue

            # 1. Match the Link Name (e.g. "LM Studio" -> "lm studio")
            name = link.get("name")
            if name:
                clean_name = name.lower().strip()
                if len(clean_name) > 2:
                    pattern = r"\b" + re.escape(clean_name) + r"\b"
                    if re.search(pattern, clean_desc) or re.search(pattern, clean_spoken):
                        print(f"[Visual Agent] Link name match found: '{clean_name}' for URL {url}. Bypassing LLM check.")
                        return url

            # 2. Match Domain/Path parts
            try:
                parsed = urllib.parse.urlparse(url)
                netloc = parsed.netloc.lower()
                if netloc.startswith("www."):
                    netloc = netloc[4:]

                path = parsed.path.lower()
                is_generic = any(gen in netloc for gen in generic_domains)

                if is_generic:
                    # Generic host (e.g. github.com) - match the path suffix (e.g. repo name "freellmapi")
                    path_parts = [p for p in path.split("/") if p]
                    if path_parts:
                        target_word = path_parts[-1]
                        if len(target_word) > 2:
                            pattern = r"\b" + re.escape(target_word) + r"\b"
                            if re.search(pattern, clean_desc) or re.search(pattern, clean_spoken):
                                print(f"[Visual Agent] Fuzzy generic domain match found: '{target_word}' for URL {url}. Bypassing LLM check.")
                                return url
                else:
                    # Specific host (e.g. ollama.com) - match the second-level domain name (e.g. "ollama")
                    domain_parts = netloc.split(".")
                    if len(domain_parts) >= 2:
                        tlds = {"com", "org", "net", "io", "ai", "co", "dev", "edu", "gov", "sh", "app", "me"}
                        domain_word = None
                        for part in domain_parts:
                            if part not in tlds and len(part) > 2:
                                domain_word = part
                                break
                        if not domain_word:
                            domain_word = domain_parts[0]

                        if domain_word and len(domain_word) > 2:
                            # Exact domain word match (e.g. "ollama")
                            pattern = r"\b" + re.escape(domain_word) + r"\b"
                            if re.search(pattern, clean_desc) or re.search(pattern, clean_spoken):
                                print(f"[Visual Agent] Fuzzy domain match found: '{domain_word}' for URL {url}. Bypassing LLM check.")
                                return url
                                
                            # Sub-word match (e.g. description has "hyper" and domain is "heyhyper")
                            words_in_text = set(re.findall(r"\b[a-z]{4,}\b", clean_desc) + re.findall(r"\b[a-z]{4,}\b", clean_spoken))
                            for w in words_in_text:
                                if w in domain_word:
                                    print(f"[Visual Agent] Sub-word domain match found: '{w}' in '{domain_word}' for URL {url}. Bypassing LLM check.")
                                    return url
            except Exception as e:
                print(f"[Visual Agent] Fuzzy parsing error for {url}: {e}")

        links_str = "\n".join([f"- {link.get('name')}: {link.get('url')}" for link in featured_links])

        system_prompt = get_system_prompt(
            "screenshot_check",
            visual_description=visual_description,
            spoken_text=spoken_text,
            featured_links=links_str
        )
        user_prompt = "Classify if this scene needs a screenshot. Output JSON now."

        try:
            res = _query_llm(self.config, system_prompt, user_prompt, task="screenshot_check", require_json=True).strip()
            res_clean = res.replace('"', '').replace("'", "").replace("`", "").strip()
            for link in featured_links:
                url = link.get("url")
                if url and url.lower() in res_clean.lower():
                    return url
        except Exception as e:
            print(f"[Visual Agent] check_if_scene_needs_screenshot failed: {e}")

        return None

    def generate_screenshot_asset(self, url, output_path):
        """
        Fetches a high-quality web screenshot for the URL via Microlink and saves it to output_path.
        """
        print(f"[Visual Agent] Generating web screenshot for {url}...")
        try:
            api_url = f"https://api.microlink.io?url={requests.utils.quote(url)}&screenshot=true&embed=screenshot.url&viewport.width=1920&viewport.height=1080"
            response = requests.get(api_url, timeout=45)
            ct = response.headers.get("Content-Type", "")
            if response.status_code == 200 and "image" in ct:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                return True
            else:
                # Log the actual Microlink error for debugging
                error_msg = response.text[:300] if "json" in ct else f"status={response.status_code}, content-type={ct}"
                raise Exception(f"Microlink API error: {error_msg}")
        except Exception as e:
            print(f"[Visual Agent] WARNING: Screenshot generation failed for {url}: {e}")
            return False

    def apply_browser_mockup(self, screenshot_path, url):
        """
        Wraps a screenshot in a beautiful, premium dark browser frame.
        """
        if not os.path.exists(screenshot_path):
            return
            
        try:
            screenshot_img = Image.open(screenshot_path)
            width, height = screenshot_img.size
            canvas = Image.new("RGB", (width, height), (30, 30, 30))
            draw = ImageDraw.Draw(canvas)
            
            header_height = int(height * 0.05)
            if header_height < 50:
                header_height = 50
                
            dot_radius = int(header_height * 0.15)
            dot_y = int(header_height * 0.5)
            dot_spacing = int(header_height * 0.4)
            start_x = int(header_height * 0.5)
            
            draw.ellipse([start_x - dot_radius, dot_y - dot_radius, start_x + dot_radius, dot_y + dot_radius], fill=(255, 95, 87))
            draw.ellipse([start_x + dot_spacing - dot_radius, dot_y - dot_radius, start_x + dot_spacing + dot_radius, dot_y + dot_radius], fill=(255, 189, 46))
            draw.ellipse([start_x + 2*dot_spacing - dot_radius, dot_y - dot_radius, start_x + 2*dot_spacing + dot_radius, dot_y + dot_radius], fill=(39, 201, 63))
            
            addr_width = int(width * 0.5)
            addr_height = int(header_height * 0.6)
            addr_x1 = int(width / 2 - addr_width / 2)
            addr_y1 = int(header_height / 2 - addr_height / 2)
            addr_x2 = addr_x1 + addr_width
            addr_y2 = addr_y1 + addr_height
            
            draw.rounded_rectangle([addr_x1, addr_y1, addr_x2, addr_y2], radius=int(addr_height * 0.2), fill=(45, 45, 45), outline=(60, 60, 60), width=1)
            
            font_size = int(addr_height * 0.5)
            windir = os.environ.get("WINDIR", "C:\\Windows")
            font_path = os.path.join(windir, "Fonts", "segoeui.ttf")
            font = None
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, font_size)
                except Exception:
                    pass
            if not font:
                font = ImageFont.load_default()
                
            url_text = url.replace("https://", "").replace("http://", "")
            draw.text((width / 2, header_height / 2), url_text, fill=(180, 180, 180), anchor="mm", font=font)
            
            screenshot_height = height - header_height
            resized_screenshot = screenshot_img.resize((width, screenshot_height), Image.Resampling.LANCZOS)
            canvas.paste(resized_screenshot, (0, header_height))
            
            canvas.save(screenshot_path, "JPEG", quality=95)
        except Exception as e:
            print(f"[Visual Agent] Failed to apply browser mockup: {e}")

    def query_llm_for_thumbnail_meta(self, topic, summary):
        """Generates a high-converting clickbait thumbnail prompt, punchy text overlay, and title suggestions."""
        system_prompt = get_system_prompt("thumbnail", topic=topic, summary=summary)
        user_prompt = "Generate the clickbait thumbnail meta JSON now."
        
        default_meta = {
            "prompt": f"A vibrant, cinematic close-up of futuristic glowing technology representing {topic}, no people, no humans, abstract neon circuitry, holographic data streams, high contrast, dramatic studio lighting, dark sleek background, premium product render.",
            "text_overlay": "MUST WATCH!",
            "title_suggestions": [
                topic,
                f"The Truth About {topic}"[:90],
                f"Is {topic} the Future?"[:90],
                f"I built a local {topic}"[:90],
                f"Stop using old workflows, use {topic}"[:90]
            ]
        }

        try:
            res = _query_llm(self.config, system_prompt, user_prompt, task="thumbnail", require_json=True)
            clean_text = res.strip()
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean_text = "\n".join(lines).strip()
            
            data = None
            try:
                data = json.loads(clean_text)
            except Exception:
                if '"prompt":' in clean_text and '"text_overlay":' in clean_text:
                    try:
                        p_start = clean_text.find('"prompt":') + len('"prompt":')
                        p_end = clean_text.find(',\n', p_start)
                        if p_end == -1:
                            p_end = clean_text.find('",', p_start)
                        p_val = clean_text[p_start:p_end].strip().strip('"').strip("'")
                        
                        t_start = clean_text.find('"text_overlay":') + len('"text_overlay":')
                        t_end = clean_text.find('}', t_start)
                        t_val = clean_text[t_start:t_end].strip().strip('"').strip("'")
                        if p_val and t_val:
                            data = {"prompt": p_val, "text_overlay": t_val, "title_suggestions": default_meta["title_suggestions"]}
                    except Exception:
                        pass
            
            if data and "prompt" in data and "text_overlay" in data:
                # Fill missing title_suggestions if parsed dict is missing them
                if "title_suggestions" not in data or not isinstance(data["title_suggestions"], list):
                    data["title_suggestions"] = default_meta["title_suggestions"]
                return data
        except Exception as e:
            print(f"[Visual Agent] LLM thumbnail meta generation failed: {e}. Using defaults.")
            
        return default_meta

    def query_llm_for_thumbnail_variants(self, topic, summary, base_meta):
        """Generate 2 additional thumbnail variants with different text overlays and title angles."""
        system_prompt = f"""You are a YouTube A/B thumbnail testing expert. Given a base thumbnail concept, generate 2 ALTERNATIVE text overlays and title sets.

RULES:
1. Each variant must have a DIFFERENT emotional angle (curiosity, urgency, fear, benefit, shock)
2. Text overlays must be 2-4 words, punchy, high-CTR
3. Titles must be under 90 chars, grammatically clean
4. NO overlap with the base text overlay: "{base_meta.get('text_overlay', 'MUST WATCH')}"
5. Output raw JSON only.

OUTPUT FORMAT:
{{
  "variants": [
    {{
      "text_overlay": "VARIANT TEXT 1",
      "title_suggestions": ["Title A1", "Title A2", "Title A3", "Title A4", "Title A5"]
    }},
    {{
      "text_overlay": "VARIANT TEXT 2",
      "title_suggestions": ["Title B1", "Title B2", "Title B3", "Title B4", "Title B5"]
    }}
  ]
}}

INPUT:
- Topic: {topic}
- Summary: {summary}
- Base text overlay (AVOID): {base_meta.get('text_overlay', 'MUST WATCH')}
- Base prompt style: {base_meta.get('prompt', '')[:200]}

OUTPUT: Raw JSON only."""

        default_variants = [
            {
                "text_overlay": "GAME CHANGER",
                "title_suggestions": [
                    f"You NEED to See This {topic}"[:90],
                    f"{topic} Changes Everything"[:90],
                    f"This {topic} Is Insane"[:90],
                    f"Why {topic} Matters Right Now"[:90],
                    f"{topic}: What Nobody Tells You"[:90]
                ]
            },
            {
                "text_overlay": "DON'T MISS THIS",
                "title_suggestions": [
                    f"I Can't Believe {topic} Works"[:90],
                    f"The {topic} Secret They Hide"[:90],
                    f"{topic} Broke My Mind"[:90],
                    f"WARNING: {topic} Is Coming"[:90],
                    f"{topic} Just Got Real"[:90]
                ]
            }
        ]

        try:
            res = _query_llm(self.config, system_prompt, "Generate 2 alternative thumbnail variants now.", task="thumbnail", require_json=True)
            clean_text = res.strip()
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean_text = "\n".join(lines).strip()

            data = json.loads(clean_text)
            variants = data.get("variants", [])
            if len(variants) >= 2:
                return variants[:2]
        except Exception as e:
            print(f"[Visual Agent] Variant generation failed: {e}. Using defaults.")

        return default_variants

    def draw_clickbait_text_overlay(self, image_path, text):
        """Uses PIL to draw clickbait text with high visibility formatting (impact font, thick outline)."""
        if not os.path.exists(image_path):
            print(f"[Visual Agent] Image path {image_path} does not exist. Skipping overlay.")
            return

        try:
            img = Image.open(image_path)
            width, height = img.size
            draw = ImageDraw.Draw(img)

            # Choose font size dynamically based on image width
            font_size = int(width * 0.09)
            if font_size < 24:
                font_size = 24

            windir = os.environ.get("WINDIR", "C:\\Windows")
            font_candidates = [
                os.path.join(windir, "Fonts", "impact.ttf"),
                os.path.join(windir, "Fonts", "Impact.ttf"),
                os.path.join(windir, "Fonts", "ariblk.ttf"), # Arial Black
                os.path.join(windir, "Fonts", "arialbd.ttf"),
                os.path.join(windir, "Fonts", "segoeuib.ttf"),
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

            words = text.split()
            lines = []
            if len(words) <= 2:
                lines.append(" ".join(words))
            else:
                lines.append(" ".join(words[:2]))
                lines.append(" ".join(words[2:]))

            line_heights = []
            for line in lines:
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    h = bbox[3] - bbox[1]
                    w = bbox[2] - bbox[0]
                except AttributeError:
                    w, h = draw.textsize(line, font=font)
                line_heights.append((line, w, h))

            total_height = sum(lh[2] for lh in line_heights) + (len(lines) - 1) * int(font_size * 0.15)
            y = height - total_height - int(height * 0.10)

            # Draw semi-transparent background bar behind text
            bar_padding = int(font_size * 0.4)
            bar_x1 = max(0, (width - max(lh[1] for lh in line_heights)) // 2 - bar_padding)
            bar_x2 = min(width, bar_x1 + max(lh[1] for lh in line_heights) + bar_padding * 2)
            bar_y1 = y - bar_padding
            bar_y2 = y + total_height + bar_padding
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rounded_rectangle([bar_x1, bar_y1, bar_x2, bar_y2], radius=12, fill=(0, 0, 0, 160))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(img)

            for line, w, h in line_heights:
                outline_w = int(font_size * 0.1)
                text_color = (255, 223, 0) # Vibrant clickbait yellow
                outline_color = (0, 0, 0)  # Heavy black stroke
                
                # Center horizontally
                x = (width - w) // 2

                try:
                    # Modern Pillow syntax
                    draw.text((x, y), line, font=font, fill=text_color, 
                              stroke_width=outline_w, stroke_fill=outline_color)
                except TypeError:
                    # Classic outline rendering fallback
                    for dx in range(-outline_w, outline_w + 1):
                        for dy in range(-outline_w, outline_w + 1):
                            if dx*dx + dy*dy <= outline_w*outline_w:
                                draw.text((x + dx, y + dy), line, font=font, fill=outline_color)
                    draw.text((x, y), line, font=font, fill=text_color)
                
                y += h + int(font_size * 0.15)

            # Save the image back
            img.save(image_path, "JPEG")
        except Exception as e:
            print(f"[Visual Agent] Failed to draw clickbait text overlay: {e}")

    def generate_thumbnail_background(self, prompt, output_path, aspect_ratio="16:9"):
        """Generates the base background image for the thumbnail using the configured/fallback provider."""
        static_bg_dir = self.config.get("static_background_dir", "")
        if static_bg_dir and os.path.isdir(static_bg_dir):
            bg_files = sorted(glob.glob(os.path.join(static_bg_dir, "*.png")) + 
                              glob.glob(os.path.join(static_bg_dir, "*.jpg")) +
                              glob.glob(os.path.join(static_bg_dir, "*.jpeg")))
            if bg_files:
                import random
                chosen = random.choice(bg_files)
                try:
                    with Image.open(chosen) as img:
                        target_w, target_h = 1280, 720
                        orig_w, orig_h = img.size
                        scale = max(target_w / orig_w, target_h / orig_h)
                        new_w = int(orig_w * scale)
                        new_h = int(orig_h * scale)
                        
                        img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        
                        left = (new_w - target_w) // 2
                        top = (new_h - target_h) // 2
                        right = left + target_w
                        bottom = top + target_h
                        
                        img_cropped = img_resized.crop((left, top, right, bottom))
                        
                        if img_cropped.mode in ("RGBA", "P"):
                            img_cropped = img_cropped.convert("RGB")
                            
                        img_cropped.save(output_path, "JPEG", quality=95)
                    self._log(f"Thumbnail: Processed and center-cropped static background {os.path.basename(chosen)} to 16:9")
                except Exception as e:
                    self._log(f"Warning: Failed to process static background {os.path.basename(chosen)}: {e}. Falling back to copy.")
                    shutil.copy2(chosen, output_path)
                return

        image_provider = self.config.get("image_provider", "pollinations")
        
        if image_provider in ("pexels_stock", "fal_video"):
            local_sd_config = self.config.get("local_sd", {})
            base_url = local_sd_config.get("base_url", "http://127.0.0.1:7860")
            
            import socket
            is_sd_active = False
            try:
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or 7860
                
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex((host, port))
                if result == 0:
                    is_sd_active = True
                sock.close()
            except Exception:
                pass
                
            if is_sd_active:
                image_provider = "local_sd"
            else:
                gemini_key = self.config.get("gemini", {}).get("api_key")
                if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY" and len(gemini_key) > 20:
                    image_provider = "gemini"
                else:
                    image_provider = "pollinations"
                    
        print(f"[Visual Agent] Generating thumbnail background using provider '{image_provider}'...")
        
        # Try primary provider, then fallbacks
        providers = [image_provider]
        if image_provider != "pollinations":
            providers.append("pollinations")
        providers.append("fallback")
        
        for provider in providers:
            try:
                if provider == "local_sd":
                    self.generate_image_local_sd(prompt, output_path, aspect_ratio)
                    return
                elif provider == "gemini":
                    self.generate_image_gemini(prompt, output_path, aspect_ratio)
                    return
                elif provider == "pollinations":
                    self.generate_image_pollinations(prompt, output_path)
                    time.sleep(3.0)
                    return
                elif provider == "fallback":
                    self.generate_fallback_image(prompt, output_path)
                    return
            except Exception as e:
                print(f"[Visual Agent] Thumbnail provider '{provider}' failed: {type(e).__name__}: {e}")
                if provider != "fallback":
                    print(f"[Visual Agent] Trying next provider...")
                continue
        
        raise Exception("All thumbnail generation providers failed")

    def generate_image_gemini(self, prompt, output_path, aspect_ratio="16:9"):
        gemini_key = self.config.get("gemini", {}).get("api_key")
        if not gemini_key or gemini_key == "YOUR_GEMINI_API_KEY":
            raise ValueError("Gemini API key is not configured.")

        from google import genai
        from google.genai import types
        client = genai.Client(api_key=gemini_key, http_options=types.HttpOptions(timeout=25000))
        model_name = self.config.get("gemini", {}).get("image_model", "imagen-3.0-generate-002")

        if "imagen" in model_name.lower():
            result = client.models.generate_images(
                model=model_name,
                prompt=prompt,
                config=dict(
                    number_of_images=1,
                    output_mime_type="image/jpeg",
                    aspect_ratio=aspect_ratio
                )
            )
            for generated_image in result.generated_images:
                image = Image.open(BytesIO(generated_image.image.image_bytes))
                image.save(output_path, "JPEG")
                return
        else:
            # Support native Gemini Image models that return images via generate_content
            response = client.models.generate_content(
                model=model_name,
                contents=f"Generate a high-quality illustration representing: '{prompt}'. The aspect ratio must be {aspect_ratio}."
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image = Image.open(BytesIO(part.inline_data.data))
                    image.save(output_path, "JPEG")
                    return
            raise ValueError(f"No image inline_data was returned by Gemini model {model_name}.")

    def generate_image_pollinations(self, prompt, output_path):
        import urllib.parse
        import requests
        import time
        
        # Clean the prompt: replace newlines, carriage returns, tabs, and remove duplicate spaces
        cleaned_prompt = prompt.replace("\n", " ").replace("\r", " ").replace("\t", " ")
        cleaned_prompt = " ".join(cleaned_prompt.split())
        
        width = self.config.get("video_settings", {}).get("width", 1920)
        height = self.config.get("video_settings", {}).get("height", 1080)
        
        encoded_prompt = urllib.parse.quote(cleaned_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true&private=true&enhance=false"
        
        max_retries = 10
        retry_delay = 5.0
        
        for attempt in range(max_retries):
            try:
                # Set a longer timeout (120s) to give the server plenty of time to process
                response = requests.get(url, timeout=120)
                
                if response.status_code == 200:
                    with open(output_path, "wb") as f:
                        f.write(response.content)
                    return
                elif response.status_code == 402 or "Queue full" in response.text:
                    print(f"[Visual Agent] Pollinations queue busy (402). Retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(retry_delay)
                    # Increase delay for backoff
                    retry_delay += 2.5
                else:
                    raise Exception(f"Pollinations API returned status {response.status_code}: {response.text[:200]}")
            except requests.RequestException as re_err:
                print(f"[Visual Agent] Network/Timeout error querying Pollinations: {re_err}. Retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay += 2.5
                
        raise Exception(f"Failed to generate image via Pollinations after {max_retries} attempts.")

    def generate_image_local_sd(self, prompt, output_path, aspect_ratio="16:9"):
        import base64
        import requests
        import urllib.parse
        
        local_sd_config = self.config.get("local_sd", {})
        base_url = local_sd_config.get("base_url", "http://127.0.0.1:7860")
        steps = local_sd_config.get("steps", 25)
        cfg_scale = local_sd_config.get("cfg_scale", 7.0)
        
        # LoRA Integration
        lora_url = local_sd_config.get("lora_url", "").strip()
        lora_strength = local_sd_config.get("lora_strength", 0.8)
        lora_trigger = local_sd_config.get("lora_trigger", "").strip()
        
        final_prompt = prompt
        
        if lora_url:
            try:
                # 1. Parse LoRA filename
                parsed_url = urllib.parse.urlparse(lora_url)
                filename = os.path.basename(parsed_url.path)
                if not filename or not filename.endswith(".safetensors"):
                    filename = "custom_lora.safetensors"
                
                # 2. Get local Lora directory path
                workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                lora_dir = os.path.join(workspace_dir, "stable-diffusion-webui", "models", "Lora")
                os.makedirs(lora_dir, exist_ok=True)
                local_lora_path = os.path.join(lora_dir, filename)
                
                # 3. Download or copy from local cache if not present
                if not os.path.exists(local_lora_path):
                    desktop_cache_dir = r"C:\Users\brica\Desktop\voxvivid\models"
                    desktop_lora_path = os.path.join(desktop_cache_dir, filename)
                    
                    if os.path.exists(desktop_lora_path):
                        print(f"[Visual Agent] LoRA file '{filename}' found in local cache: {desktop_lora_path}. Copying to Stable Diffusion...")
                        shutil.copy(desktop_lora_path, local_lora_path)
                        print(f"[Visual Agent] Copied LoRA successfully.")
                    else:
                        print(f"[Visual Agent] LoRA file '{filename}' not found locally or in cache. Downloading from {lora_url}...")
                        response = requests.get(lora_url, stream=True, timeout=900)
                        response.raise_for_status()
                        
                        total_size = int(response.headers.get('content-length', 0))
                        downloaded = 0
                        
                        with open(local_lora_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=1024*1024): # 1MB chunks
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if total_size > 0:
                                        percent = (downloaded / total_size) * 100
                                        print(f"[Visual Agent] LoRA Download: {percent:.1f}% ({downloaded / (1024*1024):.1f}MB / {total_size / (1024*1024):.1f}MB)")
                                    else:
                                        print(f"[Visual Agent] LoRA Download: {downloaded / (1024*1024):.1f}MB downloaded")
                        
                        print(f"[Visual Agent] LoRA download complete: {local_lora_path}")
                    
                    # 4. Refresh LoRAs in Stable Diffusion
                    try:
                        print("[Visual Agent] Refreshing Stable Diffusion WebUI LoRAs...")
                        refresh_url = f"{base_url}/sdapi/v1/refresh-loras"
                        requests.post(refresh_url, timeout=15)
                        print("[Visual Agent] Stable Diffusion WebUI LoRAs refreshed successfully.")
                    except Exception as re:
                        print(f"[Visual Agent] Warning: Failed to refresh SD WebUI LoRAs via API: {re}")
                else:
                    print(f"[Visual Agent] LoRA file '{filename}' already exists locally. Skipping download.")
                
                # 5. Inject LoRA syntax into prompt
                lora_stem, _ = os.path.splitext(filename)
                lora_tag = f"<lora:{lora_stem}:{lora_strength}>"
                if lora_trigger:
                    final_prompt = f"{lora_trigger}, {final_prompt}"
                final_prompt = f"{final_prompt}, {lora_tag}"
                print(f"[Visual Agent] LoRA successfully injected. Final prompt: {final_prompt}")
                
            except Exception as le:
                print(f"[Visual Agent] Error handling LoRA integration: {le}. Proceeding with original prompt.")
        
        # Decide resolution to prevent SDXL repetitions.
        if aspect_ratio == "9:16":
            width, height = 768, 1344
        else:
            width, height = 1344, 768
            
        url = f"{base_url}/sdapi/v1/txt2img"
        
        payload = {
            "prompt": final_prompt,
            "negative_prompt": "ugly, deformed, noisy, blurry, low contrast, nsfw, duplicate elements, bad anatomy, bad hands, cartoonish, lowres",
            "steps": steps,
            "cfg_scale": cfg_scale,
            "width": width,
            "height": height,
            "sampler_name": "Euler a"
        }
        
        print(f"[Visual Agent] Querying local Stable Diffusion (A1111) at: {url}...")
        response = requests.post(url, json=payload, timeout=180)
        
        if response.status_code != 200:
            raise Exception(f"Local SD WebUI API returned status {response.status_code}: {response.text[:200]}")
            
        r = response.json()
        if not r.get("images") or len(r["images"]) == 0:
            raise ValueError("No images returned from local Stable Diffusion WebUI API.")
            
        image_data = base64.b64decode(r["images"][0])
        with open(output_path, "wb") as f:
            f.write(image_data)
        print(f"[Visual Agent] Successfully saved local Stable Diffusion image to {output_path}")

    def generate_fallback_image(self, text, output_path):
        width = self.config.get("video_settings", {}).get("width", 1920)
        height = self.config.get("video_settings", {}).get("height", 1080)
        
        import random
        image = Image.new("RGB", (width, height), color=(10, 10, 18))
        draw = ImageDraw.Draw(image)
        
        # Rich gradient background with color variation
        palettes = [
            ((20, 5, 40), (5, 15, 60)),    # Deep purple to navy
            ((40, 5, 15), (10, 5, 50)),     # Dark magenta to indigo
            ((5, 25, 40), (15, 5, 35)),     # Teal to purple
            ((35, 10, 5), (5, 10, 45)),     # Warm to cool
            ((10, 20, 10), (5, 5, 40)),     # Forest to midnight
        ]
        c1, c2 = random.choice(palettes)
        
        for y in range(height):
            ratio = y / height
            r = int(c1[0] + (c2[0] - c1[0]) * ratio)
            g = int(c1[1] + (c2[1] - c1[1]) * ratio)
            b = int(c1[2] + (c2[2] - c1[2]) * ratio)
            draw.line((0, y, width, y), fill=(r, g, b))
        
        # Add diagonal accent stripes
        accent_colors = [(100, 60, 200), (200, 60, 100), (60, 120, 200), (200, 120, 40)]
        accent = random.choice(accent_colors)
        for i in range(3):
            x_off = random.randint(-200, width + 200)
            points = [(x_off, 0), (x_off + 120, 0), (x_off - height + 120, height), (x_off - height, height)]
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.polygon(points, fill=(*accent, 25))
            image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(image)
        
        # Add glowing orbs for depth
        for _ in range(random.randint(3, 6)):
            orb_x = random.randint(0, width)
            orb_y = random.randint(0, height)
            orb_r = random.randint(80, 250)
            orb_color = random.choice([(80, 40, 180), (180, 40, 80), (40, 80, 180), (180, 80, 40)])
            for r in range(orb_r, 0, -2):
                alpha = int(15 * (r / orb_r))
                c = tuple(min(255, v + alpha) for v in orb_color)
                draw.ellipse([orb_x - r, orb_y - r, orb_x + r, orb_y + r], fill=c)
        
        # Subtle grid overlay
        for x in range(0, width, 80):
            draw.line((x, 0, x, height), fill=(255, 255, 255, 8) if hasattr(draw, '_image') else (35, 35, 50), width=1)
        for y in range(0, height, 80):
            draw.line((0, y, width, y), fill=(35, 35, 50), width=1)

        image.save(output_path, "JPEG")

    def run(self, inputs):
        run_dir = inputs.get("run_dir")
        self._init_logging(run_dir)

        try:
            return self._run_inner(inputs)
        except Exception as e:
            self._log(f"FATAL ERROR: {type(e).__name__}: {e}")
            import traceback
            self._log(traceback.format_exc())
            raise
        finally:
            self._close_logging()
            self._video_manager._close_logging()

    def is_abstract_scene(self, visual_desc):
        """Detect if a visual description is too abstract for stock footage.
        Returns True if the scene should use AI image generation instead of Pexels."""
        abstract_indicators = [
            # Metaphors and analogies
            "like a", "as if", "resembles", "imagine", "think of",
            # Abstract concepts
            "data flowing", "information", "knowledge", "wisdom",
            "future", "past", "time", "space", "infinity",
            "freedom", "power", "energy", "force",
            # Poetic/metaphorical
            "dancing", "swirling", "radiating", "pulsing with",
            "glowing with", "alive with", "breathing",
            # Non-physical
            "mind", "thought", "idea", "concept", "theory",
            "dream", "vision", "soul", "spirit",
            # Complex actions hard to find stock for
            "morphing", "transforming into", "evolving into",
            "merging", "combining", "splitting apart"
        ]
        
        desc_lower = visual_desc.lower()
        
        # Check for abstract indicators
        abstract_score = 0
        for indicator in abstract_indicators:
            if indicator in desc_lower:
                abstract_score += 1
        
        # Check for concrete nouns (physical objects)
        concrete_nouns = [
            "screen", "monitor", "laptop", "desktop", "phone", "tablet",
            "keyboard", "mouse", "server", "router", "cable",
            "window", "door", "desk", "chair", "room",
            "car", "building", "street", "city",
            "person", "hand", "face", "eye",
            "chart", "graph", "dashboard", "interface",
            "code", "terminal", "command", "button"
        ]
        
        concrete_score = 0
        for noun in concrete_nouns:
            if noun in desc_lower:
                concrete_score += 1
        
        # If abstract indicators outweigh concrete nouns, it's abstract
        return abstract_score > concrete_score and abstract_score >= 2

    def detect_scene_type(self, visual_desc, spoken_text=""):
        """Detect scene type for video provider routing."""
        combined = f"{visual_desc} {spoken_text}".lower()

        hook_indicators = ["intro", "hook", "opening", "welcome", "today", "breaking", "just in", "announcement"]
        hero_indicators = ["showcase", "reveal", "demo", "launch", "main", "feature", "highlight"]
        transition_indicators = ["transition", "shift", "change", "next", "moving on", "meanwhile"]

        for indicator in hook_indicators:
            if indicator in combined:
                return "hook"
        for indicator in hero_indicators:
            if indicator in combined:
                return "hero"
        for indicator in transition_indicators:
            if indicator in combined:
                return "transition"

        return "generic"

    def _run_inner(self, inputs):
        voice_output = inputs.get("script_output")
        run_dir = inputs.get("run_dir")
        self._init_logging(run_dir)
        self._video_manager._init_logging(run_dir)
        voice_meta_path = os.path.join(run_dir, "voice_metadata.json")
        
        if not os.path.exists(voice_meta_path):
            raise FileNotFoundError(f"Voice metadata file not found at {voice_meta_path}. Cannot generate visuals without scene definitions.")

        with open(voice_meta_path, "r", encoding="utf-8") as f:
            voice_metadata = json.load(f)

        scenes = voice_metadata.get("scenes", [])
        images_dir = os.path.join(run_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        aspect_ratio = self.config.get("video_settings", {}).get("aspect_ratio", "16:9")

        generated_assets = []
        idea_output = inputs.get("idea_output", {})
        featured_links = idea_output.get("featured_links", [])

        for scene in scenes:
            idx = scene["scene_index"]
            visual_desc = scene["visual_description"]
            
            output_video_filename = f"scene_{idx}.mp4"
            output_image_filename = f"scene_{idx}.jpg"
            output_video_path = os.path.join(images_dir, output_video_filename)
            output_image_path = os.path.join(images_dir, output_image_filename)

            # Caching check: if video exists, use it. If image exists, use it.
            if os.path.exists(output_video_path):
                self._log(f"Scene {idx}: Video cached at {output_video_path}")
                generated_assets.append({
                    "scene_index": idx,
                    "raw_description": visual_desc,
                    "detailed_prompt": "Already generated (cached)",
                    "image_file": output_video_path,
                    "status": "SUCCESS",
                    "method": "cached"
                })
                continue
            elif os.path.exists(output_image_path):
                self._log(f"Scene {idx}: Image cached at {output_image_path}")
                generated_assets.append({
                    "scene_index": idx,
                    "raw_description": visual_desc,
                    "detailed_prompt": "Already generated (cached)",
                    "image_file": output_image_path,
                    "status": "SUCCESS",
                    "method": "cached"
                })
                continue

            # Check if this scene should be a website screenshot
            spoken_text = scene.get("spoken_text", "")
            screenshot_url = self.check_if_scene_needs_screenshot(visual_desc, spoken_text, featured_links)
            if screenshot_url:
                self._log(f"Scene {idx}: Matched screenshot URL: {screenshot_url}")
                if self.generate_screenshot_asset(screenshot_url, output_image_path):
                    self.apply_browser_mockup(output_image_path, screenshot_url)
                    generated_assets.append({
                        "scene_index": idx,
                        "raw_description": visual_desc,
                        "detailed_prompt": f"Web screenshot of {screenshot_url}",
                        "image_file": output_image_path,
                        "status": "SUCCESS",
                        "method": "web_screenshot"
                    })
                    continue
                else:
                    self._log(f"Scene {idx}: Screenshot failed for {screenshot_url}, falling back...")

            # Detect scene type for routing
            scene_type = self.detect_scene_type(visual_desc, spoken_text)
            scene_duration = scene.get("duration", 5.0)

            detailed_prompt = self.query_llm_for_prompt(visual_desc)
            self._log(f"Scene {idx}: aspect_ratio='{aspect_ratio}'")
            status = "FALLBACK"
            method = "pil_generator"
            final_path = output_image_path
            extra_clips = []
            pexels_success = False

            # Concrete scene — try local B-roll library first, then Pexels
            if not self.is_abstract_scene(visual_desc):
                # Check local B-roll library before hitting Pexels API
                local_broll = None
                try:
                    self._log(f"Scene {idx}: Extracting Pexels keywords from: '{visual_desc[:80]}...'")
                    pexels_keyword_list = self.query_llm_for_pexels_query(visual_desc)
                    self._log(f"Scene {idx}: Pexels keyword variations = {pexels_keyword_list}")

                    # Search local library with each keyword variation
                    for kw in pexels_keyword_list:
                        local_broll = self._search_local_broll(kw)
                        if local_broll:
                            break
                except Exception as kw_err:
                    self._log(f"Scene {idx}: Keyword extraction failed: {kw_err}")
                    pexels_keyword_list = []

                if local_broll:
                    self._log(f"Scene {idx}: B-Roll Library HIT: {local_broll}")
                    shutil.copy2(local_broll, output_video_path)
                    status = "SUCCESS"
                    method = "broll_library"
                    final_path = output_video_path
                    pexels_success = True
                else:
                    # Check if Pexels API key is valid before trying
                    pexels_key = self.config.get("pexels_api_key", "")
                    use_pexels = pexels_key and not pexels_key.startswith("${") and pexels_key != "YOUR_PEXELS_API_KEY" and pexels_key.strip() != ""
                    
                    if not use_pexels:
                        self._log(f"Scene {idx}: Pexels API key not configured. Skipping Pexels.")
                        # If SD is available, we'll use it in the image gen fallback
                    else:
                        # Fall through to Pexels API
                        if not pexels_keyword_list:
                            try:
                                pexels_keyword_list = self.query_llm_for_pexels_query(visual_desc)
                            except Exception:
                                pexels_keyword_list = ["technology abstract"]
                        
                        for kw_idx, keywords in enumerate(pexels_keyword_list):
                            try:
                                self._log(f"Scene {idx}: Trying keyword variation {kw_idx+1}: '{keywords}'")
                                self.download_pexels_video(keywords, output_video_path, aspect_ratio, target_duration=scene_duration)
                                self._log(f"Scene {idx}: Pexels video saved to {output_video_path}")
                                status = "SUCCESS"
                                method = "pexels_stock"
                                final_path = output_video_path
                                pexels_success = True
                                # Auto-populate B-roll library for future reuse
                                self._save_to_broll_library(output_video_path, keywords)
                                break
                            except Exception as kw_err:
                                self._log(f"Scene {idx}: Keyword '{keywords}' failed: {kw_err}")
                                continue
                        
                        if pexels_success:
                            # Q2: Download a 2nd clip for scenes (>3s) using a different keyword
                            if scene_duration > 3 and len(pexels_keyword_list) > 1:
                                second_keywords = pexels_keyword_list[1] if len(pexels_keyword_list) > 1 else pexels_keyword_list[0]
                                output_video_b = os.path.join(images_dir, f"scene_{idx}_b.mp4")
                                if not os.path.exists(output_video_b):
                                    try:
                                        self._log(f"Scene {idx}: Downloading 2nd clip for pacing: '{second_keywords}'")
                                        self.download_pexels_video(second_keywords, output_video_b, aspect_ratio)
                                        extra_clips.append(output_video_b)
                                        self._log(f"Scene {idx}: 2nd clip saved to {output_video_b}")
                                        time.sleep(5.0)
                                    except Exception as e2:
                                        self._log(f"Scene {idx}: 2nd clip failed (non-critical): {e2}")
                                else:
                                    extra_clips.append(output_video_b)
                                    self._log(f"Scene {idx}: 2nd clip cached at {output_video_b}")

            # Try AI video generation as a fallback only for hero shots (hook scene + 1 key scene)
            if not pexels_success:
                hero_scenes = {0}  # Scene 0 is always the hook
                if idx in hero_scenes:
                    try:
                        self._log(f"Scene {idx}: Pexels/B-roll not available. Trying AI video generation (type={scene_type})")
                        ai_video_result = self._video_manager.generate_scene(
                            scene, images_dir, run_dir
                        )
                        if ai_video_result and os.path.exists(ai_video_result["path"]):
                            self._log(f"Scene {idx}: AI video generated successfully: {ai_video_result['path']}")
                            final_path = ai_video_result["path"]
                            status = "SUCCESS"
                            method = f"ai_video_{ai_video_result['type']}"
                            pexels_success = True
                    except Exception as ai_err:
                        self._log(f"Scene {idx}: AI video generation failed: {type(ai_err).__name__}: {ai_err}")
                        import traceback
                        self._log(traceback.format_exc())

            # If still not resolved (abstract scene, or concrete with Pexels and AI video both failing/skipped), fall back to AI image gen
            if not pexels_success:
                self._log(f"Scene {idx}: Falling back to AI image generation...")
                # Check SD health first (cached) to avoid timeout on dead SD
                sd_available = self._is_sd_available()
                if sd_available:
                    self._log(f"Scene {idx}: SD available, trying local SD...")
                else:
                    self._log(f"Scene {idx}: SD unavailable (health check), skipping to Pollinations...")
                
                # Try SD first, then Pollinations, then PIL
                if sd_available:
                    try:
                        self._log(f"Scene {idx}: Trying local SD...")
                        self.generate_image_local_sd(detailed_prompt, output_image_path, aspect_ratio)
                        status = "SUCCESS"
                        method = "local_sd"
                        final_path = output_image_path
                        self._log(f"Scene {idx}: SD image saved")
                    except Exception as esd:
                        self._log(f"Scene {idx}: SD FAILED: {type(esd).__name__}: {esd}")
                        import traceback
                        self._log(traceback.format_exc())
                        sd_available = False  # Update cache for next scene
                        # Invalidate cache so next check re-tests
                        if hasattr(self, '_sd_health_cache'):
                            self._sd_health_cache['available'] = False
                if not sd_available:
                    # SD failed or was unavailable, try Pollinations
                    try:
                        self._log(f"Scene {idx}: Trying Pollinations...")
                        self.generate_image_pollinations(detailed_prompt, output_image_path)
                        status = "SUCCESS"
                        method = "pollinations"
                        final_path = output_image_path
                        self._log(f"Scene {idx}: Pollinations image saved")
                        time.sleep(3.0)
                    except Exception as ep:
                        self._log(f"Scene {idx}: Pollinations FAILED: {type(ep).__name__}: {ep}")
                        import traceback
                        self._log(traceback.format_exc())
                        self.generate_fallback_image(visual_desc, output_image_path)
                        status = "FALLBACK"
                        method = "pil_generator"
                        final_path = output_image_path
                        self._log(f"Scene {idx}: Using PIL fallback image")

            asset_entry = {
                "scene_index": idx,
                "raw_description": visual_desc,
                "detailed_prompt": detailed_prompt,
                "image_file": final_path,
                "status": status,
                "method": method
            }
            # Q2: Add extra clips for scene pacing (if available)
            if extra_clips:
                asset_entry["extra_clips"] = extra_clips
            generated_assets.append(asset_entry)

        # Generate thumbnail variants for A/B testing
        idea_output = inputs.get("idea_output", {})
        selected_topic = idea_output.get("selected_topic", "Tech Video")
        concept_summary = idea_output.get("concept_summary", "")

        self._log(f"Generating thumbnail variants for topic: '{selected_topic}'")
        base_meta = self.query_llm_for_thumbnail_meta(selected_topic, concept_summary)

        # Generate 2 additional variants
        variant_metas = self.query_llm_for_thumbnail_variants(selected_topic, concept_summary, base_meta)
        
        # Build list of all variants: [base, variant_a, variant_b]
        all_variants = [
            {"label": "a", "text_overlay": base_meta["text_overlay"], "prompt": base_meta["prompt"], "titles": base_meta.get("title_suggestions", [])},
            {"label": "b", "text_overlay": variant_metas[0]["text_overlay"], "prompt": base_meta["prompt"], "titles": variant_metas[0].get("title_suggestions", [])},
            {"label": "c", "text_overlay": variant_metas[1]["text_overlay"], "prompt": base_meta["prompt"], "titles": variant_metas[1].get("title_suggestions", [])},
        ]

        thumbnail_metadata = {
            "title_suggestions": base_meta.get("title_suggestions", [
                selected_topic,
                f"The Truth About {selected_topic}"[:90],
                f"Is {selected_topic} the Future?"[:90],
                f"I built a local {selected_topic}"[:90],
                f"Stop using old workflows, use {selected_topic}"[:90]
            ]),
            "thumbnail_prompt": base_meta["prompt"],
            "thumbnail_text": base_meta["text_overlay"],
            "has_thumbnail": False,
            "variants": [],
            "selected_variant": "a"
        }

        # Generate background image once, then create 3 variants with different text
        bg_path = os.path.join(run_dir, "thumbnail_bg_tmp.jpg")
        try:
            self._log(f"Generating shared thumbnail background...")
            self.generate_thumbnail_background(base_meta["prompt"], bg_path, aspect_ratio)
        except Exception as e:
            self._log(f"Thumbnail background generation FAILED: {type(e).__name__}: {e}")
            import traceback
            self._log(traceback.format_exc())
            bg_path = None

        for variant in all_variants:
            label = variant["label"]
            variant_path = os.path.join(run_dir, f"thumbnail_{label}.jpg")
            try:
                if bg_path and os.path.exists(bg_path):
                    # Copy background for this variant
                    from shutil import copyfile
                    copyfile(bg_path, variant_path)
                    self._log(f"Drawing text overlay '{variant['text_overlay']}' on variant {label}...")
                    self.draw_clickbait_text_overlay(variant_path, variant["text_overlay"])
                    self._log(f"Variant {label} saved: {variant_path}")
                    has_any = True
                else:
                    has_any = False
            except Exception as e:
                self._log(f"Variant {label} FAILED: {type(e).__name__}: {e}")
                has_any = False

            thumbnail_metadata["variants"].append({
                "label": label,
                "text_overlay": variant["text_overlay"],
                "title_suggestions": variant["titles"],
                "file": f"thumbnail_{label}.jpg" if bg_path else None,
                "has_thumbnail": has_any
            })

        # Clean up temp background
        if bg_path and os.path.exists(bg_path):
            os.remove(bg_path)

        # Set primary thumbnail (variant a) for backward compatibility
        primary_path = os.path.join(run_dir, "thumbnail.jpg")
        variant_a_path = os.path.join(run_dir, "thumbnail_a.jpg")
        if os.path.exists(variant_a_path):
            from shutil import copyfile
            copyfile(variant_a_path, primary_path)
            thumbnail_metadata["has_thumbnail"] = True
        else:
            thumbnail_metadata["has_thumbnail"] = False

        output_data = {
            "visual_assets": generated_assets,
            "thumbnail_metadata": thumbnail_metadata
        }

        # Save visual metadata in run directory
        visual_meta_path = os.path.join(run_dir, "visual_metadata.json")
        with open(visual_meta_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        # Save video provider usage report
        self._video_manager.save_usage_report(run_dir)

        return output_data
