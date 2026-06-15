import os
import sys
import json
import datetime
from pipeline.llm_utils import query_llm as _query_llm
from pipeline.prompts import get_system_prompt
from pipeline.seo_utils import optimize_titles, generate_hashtags, research_competition


def _safe_print(*args, **kwargs):
    try:
        text = " ".join(str(a) for a in args)
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"), **kwargs)
    except Exception:
        pass


def _normalize_text(text):
    replacements = {
        "\u2011": "-",
        "\u2013": "-",
        "\u2014": " - ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

class IdeaGeneratorAgent:
    def __init__(self, config):
        self.config = config

    def query_llm(self, system_prompt, user_prompt, require_json=False):
        return _query_llm(self.config, system_prompt, user_prompt, task="idea", require_json=require_json)

    def repair_json(self, s):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass

        fixed = []
        stack = []
        in_string = False
        escape = False
        
        i = 0
        while i < len(s):
            char = s[i]
            if char == '"' and not escape:
                in_string = not in_string
                fixed.append(char)
            elif char == '\\' and in_string:
                escape = not escape
                fixed.append(char)
                i += 1
                continue
            elif not in_string:
                if char == '{':
                    stack.append('}')
                    fixed.append(char)
                elif char == '[':
                    stack.append(']')
                    fixed.append(char)
                elif char == '}':
                    while stack:
                        top = stack.pop()
                        if top == '}':
                            break
                        else:
                            fixed.append(top)
                    fixed.append(char)
                elif char == ']':
                    while stack and stack[-1] != ']':
                        top = stack.pop()
                        fixed.append(top)
                    if stack:
                        stack.pop()
                    fixed.append(char)
                else:
                    fixed.append(char)
            else:
                fixed.append(char)
            escape = False
            i += 1
            
        while stack:
            fixed.append(stack.pop())
            
        repaired_str = "".join(fixed)
        return json.loads(repaired_str)

    def run(self, inputs):
        topic_seed = inputs.get("topic_seed", "")
        target_audience = inputs.get("target_audience", "General Audience")
        competitor_analysis = inputs.get("competitor_analysis", "")
        scraped_content = inputs.get("scraped_content")
        workspace_dir = inputs.get("workspace_dir")
        run_dir = inputs.get("run_dir")

        # Check if user supplied a script in topic_seed or scraped_content user_notes
        user_notes = ""
        if scraped_content and isinstance(scraped_content, dict):
            user_notes = scraped_content.get("user_notes", "")
        
        has_custom_script = (
            "[Narrator]" in topic_seed or "[Visual:" in topic_seed or
            "[Narrator]" in user_notes or "[Visual:" in user_notes
        )
        
        if has_custom_script:
            _safe_print("[IdeaGenerator] Custom script detected in inputs — bypassing LLM idea generation.")
            script_text = topic_seed if ("[Narrator]" in topic_seed or "[Visual:" in topic_seed) else user_notes
            
            # Query LLM to generate professional metadata (description, keywords, tags, etc.) based on the user's custom script
            concept_summary = "Running video from user-supplied custom script."
            description = "This video was generated using a custom script supplied by the user."
            keywords = ["custom script", "user video", "automation"]
            tags = ["custom", "automation", "weight and see"]
            suggested_title = None

            try:
                metadata_prompt = f"""You are an expert YouTube SEO specialist. Write metadata for a video based on the following user-supplied script:

--- SCRIPT ---
{script_text}
---

Generate the following in JSON format:
{{
  "title": "A compelling, high-CTR YouTube video title (under 70 characters) representing the script's theme.",
  "concept_summary": "A 1-2 sentence hook summarizing the video's core message.",
  "description": "An engaging, SEO-optimized YouTube video description (150-250 words) written in the channel's style. Emphasize the core message, key concepts like local AI sovereignty and model jailbreaks, and explain why this matters. Write naturally and avoid generic filler text.",
  "keywords": ["5-10 key search terms relevant to this specific video"],
  "tags": ["10-15 lowercase tags/hashtags for YouTube SEO"]
}}
"""
                _safe_print("[IdeaGenerator] Querying LLM to generate SEO description and tags for custom script...")
                meta_json = self.query_llm("You are a professional YouTube SEO specialist. Output JSON only.", metadata_prompt)
                
                # Clean up JSON if LLM returned markdown code blocks
                clean_meta = meta_json.strip()
                if clean_meta.startswith("```"):
                    lines = clean_meta.splitlines()
                    if len(lines) > 2:
                        clean_meta = "\n".join(lines[1:-1])
                
                meta_data = json.loads(clean_meta)
                suggested_title = meta_data.get("title")
                concept_summary = meta_data.get("concept_summary", concept_summary)
                description = meta_data.get("description", description)
                keywords = meta_data.get("keywords", keywords)
                tags = meta_data.get("tags", tags)
            except Exception as e:
                _safe_print(f"[IdeaGenerator] Failed to generate SEO metadata from LLM: {e}. Using generic fallbacks.")

            title = "Custom Script Video"
            if topic_seed and not ("[Narrator]" in topic_seed or "[Visual:" in topic_seed) and topic_seed.lower() != "[scraped]":
                title = topic_seed
            elif suggested_title:
                title = suggested_title
            else:
                lines = [l.strip() for l in script_text.split("\n") if l.strip()]
                for line in lines:
                    if line.startswith("[Narrator]:"):
                        title = line.replace("[Narrator]:", "").strip()
                        title_words = title.split()
                        if len(title_words) > 6:
                            title = " ".join(title_words[:6]) + "..."
                        break
            
            output_data = {
                "status": "SUCCESS",
                "selected_topic": title,
                "concept_summary": concept_summary,
                "keywords": keywords,
                "video_goal": "Generate video using custom script.",
                "description": description,
                "tags": tags,
                "featured_links": [],
                "all_concepts": [{"title": title, "swot": {"strengths": "User-supplied script", "weaknesses": "None", "opportunities": "None", "threats": "None"}}]
            }
            
            concept_path = os.path.join(run_dir, "selected_concept.json")
            with open(concept_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)
                
            return output_data


        # Continuation/rerun from prior concept when available
        selected_concept = inputs.get("selected_concept")
        if not isinstance(selected_concept, dict) or not selected_concept:
            if run_dir and os.path.isdir(run_dir):
                concept_path = os.path.join(run_dir, "selected_concept.json")
                if os.path.exists(concept_path):
                    _safe_print(f"[IdeaGenerator] Reusing prior concept from {concept_path}")
                    try:
                        with open(concept_path, "r", encoding="utf-8") as f:
                            selected_concept = json.load(f)
                        return selected_concept
                    except Exception as e:
                        _safe_print(f"[IdeaGenerator] Failed to load prior concept: {e}")

        # If seed is [scraped], derive topic from scraped content
        is_scraped_mode = topic_seed.strip().lower() == "[scraped]"
        if is_scraped_mode and scraped_content and isinstance(scraped_content, dict):
            pages = scraped_content.get("pages", [])
            base_url = scraped_content.get("base_url", "")
            total_words = scraped_content.get("total_words", 0)
            user_notes = (scraped_content.get("user_notes") or "").strip()
            
            # Check if scraped content is too thin (likely a JS SPA)
            if total_words < 500:
                _safe_print(f"[Idea Generator] Scraped content is thin ({total_words} words from {base_url})")
                
                # Deterministic topic extraction from user_notes when pages are empty
                user_notes_lower = user_notes.lower()
                forced_topic = None
                
                # Detect specific known sources and force on-topic concepts
                if "anthropic" in user_notes_lower and ("fable 5" in user_notes_lower or "mythos 5" in user_notes_lower or "government directive" in user_notes_lower):
                    forced_topic = "The Great Anthropic Recall: The US Government's Export Control Directive Against Fable 5 and Mythos 5"
                    _safe_print(f"[Idea Generator] Detected Anthropic government directive source — forcing on-topic concept")
                elif "youtube-factory" in user_notes_lower or "pipelineorchestrator" in user_notes_lower or "weight and see" in user_notes_lower:
                    forced_topic = "YouTube Factory Pipeline: A Self-Recursing Open-Source Video Production System"
                    _safe_print(f"[Idea Generator] Detected YouTube Factory source — forcing recursive meta concept")
                
                if forced_topic:
                    topic_seed = forced_topic
                else:
                    # Extract key product info from whatever we have
                    all_titles = []
                    all_headings = []
                    key_phrases = []
                    for p in pages:
                        if p.get("title"):
                            all_titles.append(p["title"])
                        for h in p.get("headings", []):
                            if h.get("text"):
                                all_headings.append(h["text"])
                        # Extract meaningful sentences from body (skip UI labels)
                        body = p.get("body", "")
                        for line in body.split("\n"):
                            line = line.strip()
                            # Skip short lines, UI labels, and placeholder text
                            if len(line) > 30 and not line.isupper() and "appear here" not in line.lower() and "no " not in line.lower():
                                key_phrases.append(line)
                    
                    title_str = ", ".join(all_titles[:3]) if all_titles else "this website"
                    phrase_str = ". ".join(key_phrases[:3]) if key_phrases else ""
                    heading_str = ", ".join(all_headings[:5]) if all_headings else ""
                    
                    topic_seed = f"Create a video about {title_str} from {base_url}"
                    if phrase_str:
                        topic_seed += f" — {phrase_str}"
                    if heading_str:
                        topic_seed += f" — features: {heading_str}"
                    _safe_print(f"[Idea Generator] Derived topic seed: {topic_seed[:150]}...")
            else:
                titles = [p.get("title", "") for p in pages if p.get("title")]
                topic_seed = f"Create a video about: {', '.join(titles[:3])} (from {base_url})"

        # 1. Load niche list from memory
        niche_list_path = os.path.join(workspace_dir, "memory", "niche_list.md")
        niche_list_content = ""
        if os.path.exists(niche_list_path):
            with open(niche_list_path, "r", encoding="utf-8") as f:
                niche_list_content = f.read()

        # 2. Get current date to ground the LLM
        current_date_str = datetime.datetime.now().strftime("%B %d, %Y")

        # 3. Dynamic trend fetching if seed is set to "trending" (skip when using scraped content)
        trends_context = ""
        past_topics_context = ""
        is_scraped_mode = topic_seed.strip().lower() == "[scraped]"
        if not is_scraped_mode and topic_seed.lower().strip() in ["trending", "latest trends", "trends", "[trends]"]:
            _safe_print("[Idea Generator] 'trending' seed detected! Fetching real-time tech trends from Hugging Face, GitHub, Hacker News, and ShowHN...")
            try:
                import requests
                from datetime import timedelta, timezone
                trending_items = []
                now_ts = datetime.datetime.now(timezone.utc).timestamp()

                ai_keywords = [
                    "ai", "llm", "llama", "gpu", "nvidia", "stable diffusion", "openai",
                    "claude", "gpt", "deep learning", "transformer", "diffusion", "embedding",
                    "fine-tune", "quantization", "rlhf", "agent", "automation", "mcp",
                    "rag", "retrieval", "multimodal", "vision", "reasoning", "benchmark",
                    "open source", "weights", "model", "inference", "edge", "local",
                    "copilot", "cursor", "gemini", "mistral", "qwen", "phi", "sonnet",
                    "opus", "o1", "o3", "sora", "midjourney", "flux", "comfy"
                ]

                # Fetch Hugging Face — trending models
                try:
                    url = "https://huggingface.co/api/models?sort=lastModified&direction=-1&limit=10"
                    res = requests.get(url, timeout=5)
                    if res.status_code == 200:
                        for m in res.json():
                            last_mod = m.get("lastModified", "")
                            hours_age = 999
                            if last_mod:
                                try:
                                    mod_ts = datetime.datetime.fromisoformat(last_mod.replace("Z", "+00:00")).timestamp()
                                    hours_age = (now_ts - mod_ts) / 3600
                                except Exception:
                                    pass
                            trending_items.append({
                                "text": f"HF Model: {m.get('id')} (Likes: {m.get('likes')})",
                                "hours_age": hours_age,
                                "source": "huggingface"
                            })
                except Exception as e:
                    _safe_print(f"[Idea Generator] HF fetch error: {e}")

                # Fetch GitHub — recent repos across AI topics (last 7 days)
                try:
                    date_str = (datetime.datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                    queries = [
                        f"created:>{date_str}+topic:llm+OR+topic:generative-ai+OR+topic:local-ai+OR+topic:agents",
                        f"created:>{date_str}+topic:text-to-image+OR+topic:stable-diffusion+OR+topic:diffusion",
                        f"created:>{date_str}+topic:voice+OR+topic:text-to-speech+OR+topic:embedding",
                    ]
                    seen_repos = set()
                    headers = {"User-Agent": "Youtube-Factory-Agent"}
                    for q in queries:
                        try:
                            url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=5"
                            res = requests.get(url, headers=headers, timeout=5)
                            if res.status_code == 200:
                                for r in res.json().get("items", []):
                                    repo_id = r.get("full_name")
                                    if repo_id not in seen_repos:
                                        seen_repos.add(repo_id)
                                        desc = r.get("description", "") or ""
                                        created_at = r.get("created_at", "")
                                        hours_age = 999
                                        if created_at:
                                            try:
                                                cat_ts = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                                                hours_age = (now_ts - cat_ts) / 3600
                                            except Exception:
                                                pass
                                        trending_items.append({
                                            "text": f"GitHub Repo: {repo_id} - {desc[:100]} (Stars: {r.get('stargazers_count')})",
                                            "hours_age": hours_age,
                                            "source": "github"
                                        })
                        except Exception:
                            pass
                except Exception as e:
                    _safe_print(f"[Idea Generator] GitHub fetch error: {e}")

                # Fetch HackerNews — broader AI keyword filter
                try:
                    url = "https://hacker-news.firebaseio.com/v0/topstories.json"
                    res = requests.get(url, timeout=5)
                    if res.status_code == 200:
                        def fetch_hn_item(item_id):
                            try:
                                item_url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
                                item_res = requests.get(item_url, timeout=3)
                                if item_res.status_code == 200:
                                    item_data = item_res.json()
                                    title = item_data.get("title", "")
                                    if any(kw in title.lower() for kw in ai_keywords):
                                        item_time = item_data.get("time", 0)
                                        hours_age = (now_ts - item_time) / 3600 if item_time else 999
                                        return {
                                            "text": f"HackerNews: {title}",
                                            "hours_age": hours_age,
                                            "source": "hackernews"
                                        }
                            except Exception:
                                pass
                            return None

                        from concurrent.futures import ThreadPoolExecutor, as_completed
                        with ThreadPoolExecutor(max_workers=10) as ex:
                            futures = [ex.submit(fetch_hn_item, item_id) for item_id in res.json()[:50]]
                            for f in as_completed(futures):
                                result = f.result()
                                if result:
                                    trending_items.append(result)
                except Exception as e:
                    _safe_print(f"[Idea Generator] HackerNews fetch error: {e}")

                # Fetch ShowHN — developer announcements (new tools, models, launches)
                try:
                    url = "https://hacker-news.firebaseio.com/v0/showstories.json"
                    res = requests.get(url, timeout=5)
                    if res.status_code == 200:
                        def fetch_show_hn_item(item_id):
                            try:
                                item_url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
                                item_res = requests.get(item_url, timeout=3)
                                if item_res.status_code == 200:
                                    item_data = item_res.json()
                                    title = item_data.get("title", "")
                                    if any(kw in title.lower() for kw in ai_keywords):
                                        item_time = item_data.get("time", 0)
                                        hours_age = (now_ts - item_time) / 3600 if item_time else 999
                                        return {
                                            "text": f"ShowHN: {title}",
                                            "hours_age": hours_age,
                                            "source": "showhn"
                                        }
                            except Exception:
                                pass
                            return None

                        with ThreadPoolExecutor(max_workers=10) as ex:
                            futures = [ex.submit(fetch_show_hn_item, item_id) for item_id in res.json()[:30]]
                            for f in as_completed(futures):
                                result = f.result()
                                if result:
                                    trending_items.append(result)
                except Exception as e:
                    _safe_print(f"[Idea Generator] ShowHN fetch error: {e}")

                # Sort by recency — most recent first (newsgacking priority)
                trending_items.sort(key=lambda x: x.get("hours_age", 999))

                if trending_items:
                    # Build a rich seed from actual trending items instead of generic string
                    top_items = trending_items[:8]
                    topic_seed = f"Latest AI trends in 2026: {', '.join([item['text'].split(': ', 1)[-1].split(' (')[0][:40] for item in top_items[:5]])}"
                    # Add recency labels to context
                    labeled_items = []
                    for item in trending_items:
                        h = item.get("hours_age", 999)
                        if h < 12:
                            label = f"[HOT - {h:.0f}h ago]"
                        elif h < 24:
                            label = f"[FRESH - {h:.0f}h ago]"
                        else:
                            label = f"[older]"
                        labeled_items.append(f"{label} {item['text']}")
                    trends_context = "\n- Live Real-time Tech Trends (fetched moments ago):\n" + "\n".join(labeled_items)
            except Exception as ex:
                _safe_print(f"[Idea Generator] Failed to fetch real-time trends: {ex}")

        # 3b. Load past topics to avoid repetition
        try:
            runs_dir = os.path.join(workspace_dir, "runs")
            if os.path.exists(runs_dir):
                past_titles = []
                for run_name in sorted(os.listdir(runs_dir), reverse=True)[:10]:
                    concept_path = os.path.join(runs_dir, run_name, "selected_concept.json")
                    if os.path.exists(concept_path):
                        with open(concept_path, "r", encoding="utf-8") as f:
                            concept = json.load(f)
                            title = concept.get("selected_concept", {}).get("title", "")
                            if title:
                                past_titles.append(title)
                if past_titles:
                    past_topics_context = "\n- PREVIOUS VIDEO TOPICS (DO NOT REPEAT THESE):\n" + "\n".join(f"  - {t}" for t in past_titles)
        except Exception as e:
            _safe_print(f"[Idea Generator] Failed to load past topics: {e}")

        # Format scraped content if available
        scraped_context = ""
        if scraped_content and isinstance(scraped_content, dict):
            pages = scraped_content.get("pages", []) or []
            total_words = int(scraped_content.get("total_words", 0) or 0)
            base_url = scraped_content.get("base_url", "") or ""
            user_notes = scraped_content.get("user_notes", "") or ""

            usable_source = bool(pages or user_notes.strip())
            if not usable_source:
                scraped_context = (
                    f"\n\n=== SOURCE MATERIAL ===\n"
                    f"- Source: {base_url}\n"
                    "- Note: No parseable page body was extracted from this source.\n"
                )

            if pages:
                # Filter out low-value pages
                skip_keywords = ["terms of service", "privacy policy", "terms of use",
                                 "cookie", "legal", "login", "sign up", "register",
                                 "contact us", "about us", "faq"]
                useful_pages = []
                for p in pages:
                    title_lower = (p.get("title") or "").lower()
                    body_lower = (p.get("body") or "")[:200].lower()
                    if not any(kw in title_lower or kw in body_lower for kw in skip_keywords):
                        if int(p.get("word_count", 0) or 0) >= 50:
                            useful_pages.append(p)

                if useful_pages:
                    scraped_parts = [
                        "\n\n=== SOURCE MATERIAL (ONLY USE THIS — DO NOT USE TRENDING DATA) ===",
                        f"Website: {base_url}",
                        f"Articles: {len(useful_pages)} usable pages",
                        "",
                    ]
                    if user_notes.strip():
                        scraped_parts.extend([
                            "--- USER NOTES (README/docs/extra context) ---",
                            user_notes[:4000],
                            "",
                        ])
                    scraped_parts.extend([
                        "RULES: ALL 3 video concepts MUST be based on the source material below.",
                        "Use tool/model names, claims, and quotes from the source.",
                        "If the source mentions a government action, model recall, or security directive, make that the video focus.",
                        "",
                    ])
                    for i, page in enumerate(useful_pages[:5]):
                        title = page.get("title") or "Untitled"
                        body_preview = (page.get("body") or "")[:1500]
                        url = page.get("url") or base_url
                        scraped_parts.append(f"--- Article {i+1}: {title} ---")
                        scraped_parts.append(f"URL: {url}")
                        scraped_parts.append(body_preview)
                        scraped_parts.append("")
                    scraped_context = "\n".join(scraped_parts)

            if not scraped_context and user_notes.strip():
                scraped_context = (
                    "\n\n=== SOURCE MATERIAL (ONLY USE THIS — DO NOT USE TRENDING DATA) ===\n"
                    f"Source: {base_url}\n"
                    "--- USER NOTES (README/docs/extra context) ---\n"
                    f"{user_notes[:4000]}\n"
                )

        # 4. Build system prompt from centralized registry with dynamic data injection
        topic_for_prompt = topic_seed
        # Bias concept generation toward the real source content when in scraped mode
        if is_scraped_mode and 'user_notes' in dir() and user_notes.strip():
            topic_for_prompt = (
                f"Generate concepts specifically about this source text: {base_url}. "
                f"Use claims, organizations, product/model names, and quoted details from the USER NOTES below. "
                f"Do not pivot the topic toward unrelated GPUs or general AI news. "
                f"Source title hint: {all_titles[0] if 'all_titles' in dir() and all_titles else base_url}."
            )

        system_prompt = get_system_prompt(
            "idea",
            current_date=current_date_str,
            topic_seed=topic_for_prompt,
            target_audience=target_audience,
            competitor_analysis=competitor_analysis,
            niche_list_content=niche_list_content,
            trends_context=trends_context,
            past_topics_context=past_topics_context,
            scraped_content=scraped_context
        )

        # Simple user prompt - the dynamic data is in the system prompt
        user_prompt = "Generate the video concept JSON now."

        response_text = self.query_llm(system_prompt, user_prompt)
        
        # Clean up JSON if LLM returned markdown code blocks
        clean_text = response_text.strip()
        if clean_text.startswith("```"):
            lines = clean_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_text = "\n".join(lines).strip()

        # Parse JSON to confirm validation and store, attempting auto-repair if malformed
        try:
            output_data = self.repair_json(clean_text)
        except Exception as e:
            raise ValueError(f"Failed to parse JSON response from LLM after auto-repair: {str(e)}\nResponse was: {response_text}")

        # Post-process text fields to replace outdated years (2024/2025 -> 2026)
        # Only touch text fields — never URLs, version strings, or numeric identifiers
        def clean_years(val):
            if isinstance(val, str):
                return val.replace("2024", "2026").replace("2025", "2026")
            return val

        for field in ["selected_topic", "concept_summary", "video_goal"]:
            if field in output_data:
                output_data[field] = clean_years(output_data[field])
        if "keywords" in output_data:
            output_data["keywords"] = [clean_years(k) for k in output_data["keywords"]]

        # Fix common LLM typos in tech product names
        title = output_data.get("selected_topic", "")
        typo_fixes = {
            "P Zero": "Pi Zero",
            "P  Zero": "Pi Zero",
            "G P T": "GPT",
            "G P U": "GPU",
            "L L M": "LLM",
            "R T X": "RTX",
            "C P U": "CPU",
            "S D L": "SDL",
            "A I": "AI",
            "V L L M": "vLLM",
            "O L L A M A": "Ollama",
        }
        for wrong, right in typo_fixes.items():
            title = title.replace(wrong, right)
        output_data["selected_topic"] = title

        # Validate that selected_topic contains a real tool/model from trending data
        if trends_context:
            # Extract tool names from the trending context (lines starting with [HOT], [FRESH], etc.)
            import re
            real_tools = set()
            for line in trends_context.split("\n"):
                # Extract the tool/model name after the source prefix
                match = re.search(r'(?:HackerNews|ShowHN|HF Model|GitHub Repo):\s*(.+?)(?:\s*\(|$)', line)
                if match:
                    name = match.group(1).strip()
                    # Take first 3 words as the tool name
                    words = name.split()[:3]
                    real_tools.add(" ".join(words).lower())
            # Check if any real tool name appears in the selected topic
            topic_lower = title.lower()
            has_real_tool = any(tool in topic_lower for tool in real_tools if len(tool) > 3)
            if not has_real_tool and real_tools:
                _safe_print(f"[Idea Generator] WARNING: Selected topic may contain hallucinated tool name: '{title}'")
                _safe_print(f"[Idea Generator] Real tools from trending data: {list(real_tools)[:5]}")

        # Validate featured_links — remove any URLs that return 404 or are unreachable
        featured_links = output_data.get("featured_links", [])
        if featured_links:
            import requests as _req
            valid_links = []
            for link in featured_links:
                url = link.get("url", "")
                if not url:
                    continue
                try:
                    resp = _req.head(url, timeout=8, allow_redirects=True)
                    if resp.status_code < 400:
                        valid_links.append(link)
                    else:
                        _safe_print(f"[Idea Generator] Removing dead link ({resp.status_code}): {url}")
                except Exception:
                    _safe_print(f"[Idea Generator] Removing unreachable link: {url}")
            output_data["featured_links"] = valid_links

        # Flag banned/outdated model names
        banned_models = ["gpt-4o", "gpt-4", "claude 3", "claude 3.5", "gemini 1.0", "gemini 2.0", "llama 3", "llama 3.1", "llama 3.2"]
        selected = output_data.get("selected_topic", "").lower()
        concept = output_data.get("concept_summary", "").lower()
        for banned in banned_models:
            if banned in selected or banned in concept:
                _safe_print(f"[Idea Generator] WARNING: Topic contains outdated model '{banned}' — prompt should have caught this")

        # SEO Optimization Pass
        try:
            selected_topic = output_data.get("selected_topic", "")
            keywords = output_data.get("keywords", [])
            
            # Research competition for the topic
            _safe_print(f"[Idea Generator] Running SEO research for: {selected_topic}")
            competition_data = research_competition(selected_topic)
            output_data["seo_research"] = competition_data
            
            # Generate optimized hashtags
            hashtags = generate_hashtags(selected_topic, keywords)
            output_data["optimized_hashtags"] = hashtags
            
            # Collect all candidate titles (from LLM concepts + thumbnail suggestions will come later)
            candidate_titles = [output_data.get("selected_topic", "")]
            for concept in output_data.get("all_concepts", []):
                if concept.get("title"):
                    candidate_titles.append(concept["title"])
            
            # Score and rank titles
            if len(candidate_titles) > 1:
                scored_titles = optimize_titles(candidate_titles, keywords, max_results=3)
                output_data["seo_scored_titles"] = scored_titles
                
                # Use the top-scoring title as selected_topic if it's significantly better
                if scored_titles and scored_titles[0]["score"] > 70:
                    best_title = scored_titles[0]["title"]
                    if best_title != output_data.get("selected_topic"):
                        _safe_print(f"[Idea Generator] SEO optimized title: '{best_title}' (score: {scored_titles[0]['score']})")
                        output_data["selected_topic_original"] = output_data["selected_topic"]
                        output_data["selected_topic"] = best_title
            
            _safe_print(f"[Idea Generator] SEO hashtags: {hashtags}")
        except Exception as e:
            _safe_print(f"[Idea Generator] SEO optimization failed (non-fatal): {e}")

        # Save concept details to run directory
        concept_path = os.path.join(run_dir, "selected_concept.json")
        with open(concept_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        return output_data

