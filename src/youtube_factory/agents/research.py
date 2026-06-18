from youtube_factory.logging_utils import get_logger

log = get_logger("agent_research")
import os
import json
import re
import requests
from urllib.parse import urlparse
from youtube_factory.llm import query_llm as _query_llm
from youtube_factory.prompts import get_system_prompt

# Agent-Reach Channels
from agent_reach.channels.youtube import YouTubeChannel
from agent_reach.channels.web import WebChannel
from agent_reach.channels.github import GitHubChannel
from agent_reach.channels.exa_search import ExaSearchChannel
from agent_reach.channels.rss import RSSChannel
from agent_reach.channels.twitter import TwitterChannel
from agent_reach.channels.reddit import RedditChannel
from agent_reach.channels.bilibili import BilibiliChannel
from agent_reach.channels.xiaoyuzhou import XiaoyuzhouChannel


class ResearchAgent:
    def __init__(self, config):
        self.config = config
        self.github_token = config.get("github", {}).get("api_key") or os.getenv("GITHUB_TOKEN")
        self.session = requests.Session()
        if self.github_token:
            self.session.headers.update({"Authorization": f"token {self.github_token}"})
        self.session.headers.update({"User-Agent": "YouTube-Factory-Research-Agent"})
        
        # Initialize Agent-Reach channels
        self.youtube_channel = YouTubeChannel()
        self.web_channel = WebChannel()
        self.github_channel = GitHubChannel()
        self.exa_search_channel = ExaSearchChannel()
        self.rss_channel = RSSChannel()
        self.twitter_channel = TwitterChannel()
        self.reddit_channel = RedditChannel()
        self.bilibili_channel = BilibiliChannel()
        self.xiaoyuzhou_channel = XiaoyuzhouChannel()

        # Agent-Reach handles PATH and underlying tools, so remove hardcoded paths
        self._env = os.environ.copy()

    def _run_cmd(self, cmd, timeout=60, parse_json=False):
        """Run a shell command and return stdout, optionally parsing JSON."""
        import subprocess
        log.info(f"[Research Agent] Running command: {' '.join(cmd[:5])}...")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env,
                check=False
            )
            if result.returncode != 0:
                log.warning(f"Command failed (code {result.returncode}): {result.stderr[:200]}")
                return None
            stdout = result.stdout.strip()
            if parse_json:
                import json
                return json.loads(stdout) if stdout else None
            return stdout
        except subprocess.TimeoutExpired:
            log.error(f"Command timed out after {timeout}s: {' '.join(cmd)}")
            return None
        except Exception as e:
            log.error(f"Command failed: {e}")
            return None

    def _fetch_youtube_transcript(self, url):
        """Fetch YouTube transcript using Agent-Reach YouTubeChannel."""
        log.info(f"[Research Agent] Fetching YouTube transcript via Agent-Reach: {url}")
        transcript = self.youtube_channel.transcribe(url)
        if not transcript:
            log.warning("Failed to get YouTube transcript via Agent-Reach.")
            # Fallback to yt-dlp if Agent-Reach fails?
            # For now, proceed without transcript if Agent-Reach fails.
            transcript = ""

        # Keep yt-dlp for metadata (title, description, duration, channel, video_id)
        # as YouTubeChannel.transcribe() doesn't seem to provide it.
        cmd = [
            "yt-dlp",
            "--write-auto-sub",
            "--sub-lang", "en",
            "--skip-download",
            "--print-json",
            "--no-warnings",
            url
        ]
        result = self._run_cmd(cmd, timeout=120, parse_json=True)
        if result:
            # Combine transcript with metadata
            return {
                "type": "youtube",
                "title": result.get("title", ""),
                "content": transcript or result.get("description", "")[:8000],
                "duration": result.get("duration", 0),
                "channel": result.get("uploader", ""),
                "url": url,
                "video_id": result.get("id", "")
            }
        else:
            # If yt-dlp also fails, return what we have (transcript only)
            return {
                "type": "youtube",
                "title": "Unknown Title",
                "content": transcript,
                "duration": 0,
                "channel": "Unknown Channel",
                "url": url,
                "video_id": ""
            }

    def _fetch_youtube_subtitles(self, url):
        """Fetch YouTube subtitles using yt-dlp."""
        log.info(f"[Research Agent] Fetching YouTube subtitles via yt-dlp: {url}")
        cmd = [
            "yt-dlp",
            "--write-auto-sub",
            "--sub-lang", "en",
            "--sub-format", "vtt",
            "--skip-download",
            "--no-warnings",
            "-o", "-",  # output to stdout
            url
        ]
        result = self._run_cmd(cmd, timeout=120)
        if result:
            # Parse VTT format
            lines = result.splitlines()
            text_lines = []
            for line in lines:
                if line and not line.startswith("WEBVTT") and not line.strip().replace(".", "").isdigit() and "-->" not in line:
                    text_lines.append(line)
            transcript = " ".join(text_lines)
            return transcript[:15000] if transcript else None
        return None

    def _fetch_web_article(self, url):
        """Fetch web article using Agent-Reach WebChannel."""
        log.info(f"[Research Agent] Fetching web article via Agent-Reach: {url}")
        try:
            content = self.web_channel.read(url)
            if content and len(content) > 100:
                return {
                    "type": "article",
                    "title": "",
                    "content": content[:8000],
                    "url": url
                }
        except Exception as e:
            log.info(f"[Research Agent] WebChannel read failed for {url}: {e}")
        return None

    def _search_exa(self, query):
        """Semantic web search using Exa via mcporter MCP."""
        log.info(f"[Research Agent] Exa search via mcporter: {query}")
        # Use mcporter to call Exa MCP with flag-style args
        # Agent-Reach should handle mcporter configuration
        cmd = [
            "mcporter.cmd", "call", "exa.web_search_exa",
            f'query={query}', 'numResults=5'
        ]
        result = self._run_cmd(cmd, timeout=60)
        if result:
            # mcporter outputs text format, parse it
            formatted = []
            # Split by --- separator
            for section in result.split("\n---\n"):
                if section.strip():
                    formatted.append(section.strip())
            return {
                "type": "search",
                "query": query,
                "content": "\n\n---\n\n".join(formatted[:5]),
                "results": []
            }
        return None

    def _fetch_rss(self, url):
        """Fetch RSS feed using Agent-Reach RSSChannel."""
        log.info(f"[Research Agent] Fetching RSS via Agent-Reach: {url}")
        try:
            content = self.rss_channel.read(url)
            if content:
                return {
                    "type": "rss",
                    "title": "",
                    "content": content[:8000],
                    "entries": []
                }
        except Exception as e:
            log.info(f"[Research Agent] RSSChannel read failed for {url}: {e}")
        return None

    def _safe_get(self, url, **kwargs):
        """Safe GET with timeout and error handling."""
        try:
            resp = self.session.get(url, timeout=15, **kwargs)
            if resp.status_code == 200:
                return resp
        except Exception as e:
            log.info(f"[Research Agent] Request failed for {url}: {e}")
        return None

    def _extract_github_info(self, url):
        """Extract owner/repo from GitHub URL."""
        patterns = [
            r"github\.com/([^/]+)/([^/?#]+)",
            r"github\.com/([^/]+)/([^/?#]+)\.git",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                owner, repo = match.groups()
                repo = repo.replace(".git", "")
                return owner, repo
        return None, None

    def _fetch_github_repo(self, owner, repo):
        """Fetch GitHub repo metadata using Agent-Reach GitHubChannel."""
        log.info(f"[Research Agent] Fetching GitHub repo via Agent-Reach: {owner}/{repo}")
        repo_info = self.github_channel.get_repo_info(owner, repo)
        if repo_info:
            return {
                "type": "github",
                "title": repo_info.get("name", repo),
                "content": repo_info.get("description", "")[:3000],
                "url": repo_info.get("url", ""),
                "stars": repo_info.get("stargazerCount", 0),
                "language": repo_info.get("primaryLanguage", {}).get("name", "") if repo_info.get("primaryLanguage") else ""
            }
        return None

    def _fetch_github_readme(self, owner, repo):
        """Fetch README content using Agent-Reach GitHubChannel or API fallback."""
        log.info(f"[Research Agent] Fetching GitHub README: {owner}/{repo}")
        
        # Try Agent-Reach channel first
        try:
            if hasattr(self.github_channel, 'get_readme'):
                readme = self.github_channel.get_readme(owner, repo)
                if readme:
                    return readme
        except Exception as e:
            log.info(f"[Research Agent] Agent-Reach get_readme failed: {e}")
        
        # Fallback to direct GitHub API
        import requests
        headers = {"Accept": "application/vnd.github+json"}
        github_token = self.config.get("github", {}).get("api_key") or self.config.get("github", {}).get("token") or self.config.get("GITHUB_TOKEN")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        
        try:
            r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/readme", headers=headers, timeout=15)
            if r.status_code == 200:
                import base64
                readme_data = r.json()
                return base64.b64decode(readme_data.get("content", "")).decode("utf-8", errors="ignore")
        except Exception as e:
            log.info(f"[Research Agent] GitHub API readme fetch error: {e}")
        
        return None

    def _fetch_github_contents(self, owner, repo, path=""):
        """Fetch directory contents using Agent-Reach GitHubChannel or API fallback."""
        log.info(f"[Research Agent] Fetching GitHub contents: {owner}/{repo}/{path}")
        
        # Try Agent-Reach channel first
        try:
            if hasattr(self.github_channel, 'get_contents'):
                contents = self.github_channel.get_contents(owner, repo, path)
                if contents:
                    return contents
        except Exception as e:
            log.info(f"[Research Agent] Agent-Reach get_contents failed: {e}")
        
        # Fallback to direct GitHub API
        import requests
        headers = {"Accept": "application/vnd.github+json"}
        github_token = self.config.get("github", {}).get("api_key") or self.config.get("github", {}).get("token") or self.config.get("GITHUB_TOKEN")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        
        try:
            api_path = path.strip("/") if path else ""
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{api_path}" if api_path else f"https://api.github.com/repos/{owner}/{repo}/contents"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log.info(f"[Research Agent] GitHub API contents fetch error: {e}")
        
        return None

    def _fetch_key_files(self, owner, repo, max_files=8):
        """Fetch a few key source files for context."""
        contents = self._fetch_github_contents(owner, repo)
        if not contents:
            return {}

        priority_patterns = [
            r"(main|app|index|cli|server|api)\.(py|js|ts|go|rs)",
            r"(config|settings|constants)\.(py|js|json|yaml|yml|toml)",
            r"(model|agent|pipeline|core)\.(py|js|ts)",
            r"requirements\.txt|pyproject\.toml|package\.json|Cargo\.toml|go\.mod",
            r"Dockerfile|docker-compose\.yml",
        ]

        files_to_fetch = []
        for item in contents:
            if item["type"] == "file":
                name = item["name"]
                for pattern in priority_patterns:
                    if re.search(pattern, name, re.I):
                        files_to_fetch.append(item)
                        break
                if len(files_to_fetch) >= max_files:
                    break

        for item in contents:
            if item["type"] == "dir" and item["name"] in ("src", "lib", "core", "agents", "pipeline", "models"):
                sub_contents = self._fetch_github_contents(owner, repo, item["name"])
                if sub_contents:
                    for sub_item in sub_contents[:3]:
                        if sub_item["type"] == "file" and sub_item["name"].endswith((".py", ".js", ".ts", ".rs", ".go")):
                            files_to_fetch.append(sub_item)
                            if len(files_to_fetch) >= max_files:
                                break

        file_contents = {}
        for item in files_to_fetch[:max_files]:
            resp = self._safe_get(item["download_url"])
            if resp and resp.text:
                content = resp.text[:3000]
                file_contents[item["path"]] = content
        return file_contents

    def _fetch_gh_repo(self, owner, repo):
        """Fetch GitHub repo using GitHub API (fallback if Agent-Reach channel lacks methods)."""
        log.info(f"[Research Agent] Fetching GitHub repo via API: {owner}/{repo}")
        
        # Try Agent-Reach channel first (if methods exist)
        repo_info = None
        readme = None
        try:
            if hasattr(self.github_channel, 'get_repo_info'):
                repo_info = self.github_channel.get_repo_info(owner, repo)
            if hasattr(self.github_channel, 'get_readme'):
                readme = self.github_channel.get_readme(owner, repo)
        except Exception as e:
            log.info(f"[Research Agent] Agent-Reach GitHubChannel failed: {e}")
        
        # Fallback to direct GitHub API
        if repo_info is None:
            repo_info, readme = self._fetch_gh_repo_api(owner, repo)
        
        if repo_info:
            return {
                "type": "github",
                "title": repo_info.get("name", repo),
                "content": (readme or "")[:8000] or repo_info.get("description", ""),
                "url": repo_info.get("html_url", ""),
                "stars": repo_info.get("stargazers_count", 0),
                "language": repo_info.get("language", "")
            }
        return None

    def _fetch_gh_repo_api(self, owner, repo):
        """Fetch GitHub repo info and README directly via GitHub REST API."""
        import requests
        headers = {"Accept": "application/vnd.github+json"}
        github_token = self.config.get("github", {}).get("api_key") or self.config.get("github", {}).get("token") or self.config.get("GITHUB_TOKEN")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        
        base_url = f"https://api.github.com/repos/{owner}/{repo}"
        
        # Get repo info
        try:
            r = requests.get(base_url, headers=headers, timeout=15)
            if r.status_code == 200:
                repo_info = r.json()
            else:
                log.info(f"[Research Agent] GitHub API repo fetch failed: {r.status_code}")
                return None, None
        except Exception as e:
            log.info(f"[Research Agent] GitHub API repo fetch error: {e}")
            return None, None
        
        # Get README
        readme = ""
        try:
            r = requests.get(f"{base_url}/readme", headers=headers, timeout=15)
            if r.status_code == 200:
                import base64
                readme_data = r.json()
                readme = base64.b64decode(readme_data.get("content", "")).decode("utf-8", errors="ignore")
        except Exception:
            pass
        
        return repo_info, readme

    def _fetch_generic_url(self, url):
        """Fetch and extract content from generic URL using trafilatura if available."""
        try:
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                extracted = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
                if extracted:
                    return {"type": "article", "content": extracted[:8000], "title": trafilatura.extract_metadata(downloaded).title if trafilatura.extract_metadata(downloaded) else ""}
        except ImportError:
            pass
        except Exception as e:
            log.info(f"[Research Agent] Trafilatura failed: {e}")

        resp = self._safe_get(url)
        if resp:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            title = soup.title.string if soup.title else ""
            return {"type": "webpage", "content": text[:8000], "title": title}
        return None

    def _summarize_with_llm(self, source_type, title, content, url, key_files=None):
        """Use LLM to create structured summary."""
        system_prompt = get_system_prompt("research")

        context_parts = [
            f"SOURCE URL: {url}",
            f"SOURCE TYPE: {source_type}",
            f"TITLE: {title}",
            f"CONTENT:\n{content[:6000]}",
        ]
        if key_files:
            context_parts.append("KEY SOURCE FILES:")
            for path, file_content in key_files.items():
                context_parts.append(f"\n--- {path} ---\n{file_content}")

        user_prompt = "\n\n".join(context_parts) + "\n\nProduce the structured research JSON now."

        try:
            response = _query_llm(
                self.config,
                system_prompt,
                user_prompt,
                task="research",
                require_json=True
            )
            clean = response.strip()
            if clean.startswith("```"):
                lines = clean.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean = "\n".join(lines).strip()
            try:
                return json.loads(clean)
            except json.JSONDecodeError as decode_err:
                log.info(f"[Research Agent] JSON parsing failed: {decode_err}")
                log.info(f"[Research Agent] Raw LLM response on parse failure:\n{clean}")
                raise
        except Exception as e:
            log.info(f"[Research Agent] LLM summarization failed: {e}")
            return None

    def run(self, inputs):
        """Main entry point."""
        topic_seed = inputs.get("topic_seed", "").strip()
        run_dir = inputs.get("run_dir")
        workspace_dir = inputs.get("workspace_dir")

        # 1. Attempt to extract a URL or GitHub repo pattern
        url_match = re.search(r"https?://[^\s]+", topic_seed)
        if url_match:
            topic_seed = url_match.group(0)
            is_url = True
        else:
            # Check for GitHub owner/repo format in the seed
            github_repo_match = re.search(
                r"(?:^|\s)([a-zA-Z0-9-]{1,39})/([a-zA-Z0-9._-]{1,100})(?:\s|$| -)",
                topic_seed
            )
            if github_repo_match:
                owner, repo = github_repo_match.groups()
                # Ignore common words or file-like patterns
                if repo.lower() not in ("and", "or", "with", "for", "the", "a", "an") and not repo.endswith(('.py', '.js', '.ts', '.html', '.css', '.txt', '.md', '.png', '.jpg', '.jpeg', '.gif')):
                    topic_seed = f"https://github.com/{owner}/{repo}"
                    log.info(f"[Research Agent] Found GitHub repo in topic seed, converted to URL: {topic_seed}")
                    is_url = True
                else:
                    is_url = False
            else:
                is_url = False

        research_data = {
            "source_url": topic_seed if is_url else "",
            "project_name": "",
            "one_liner": "",
            "tech_stack": [],
            "key_features": [],
            "innovation_hooks": [],
            "demo_ideas": [],
            "code_snippets": [],
            "architecture_notes": "",
            "readme_summary": "",
            "is_url_source": is_url
        }

        if not is_url:
            # 2. Web Search Fallback using Exa
            log.info(f"[Research Agent] Query is not a URL/GitHub repo. Performing web search: {topic_seed}")
            search_result = self._search_exa(topic_seed)
            if search_result and search_result.get("content"):
                llm_result = self._summarize_with_llm(
                    "search", f"Web Search: {topic_seed}", search_result["content"], topic_seed
                )
                if llm_result:
                    research_data.update(llm_result)

                    if run_dir:
                        research_path = os.path.join(run_dir, "research.json")
                        with open(research_path, "w", encoding="utf-8") as f:
                            json.dump(research_data, f, indent=2)
                        log.info(f"[Research Agent] Saved research to {research_path}")
            return research_data

        log.info(f"[Research Agent] Analyzing URL: {topic_seed}")

        parsed = urlparse(topic_seed)
        hostname = parsed.netloc.lower()

        # YouTube - use yt-dlp for transcript extraction
        if "youtube.com" in hostname or "youtu.be" in hostname:
            result = self._fetch_youtube_transcript(topic_seed)
            subtitles = self._fetch_youtube_subtitles(topic_seed)
            if result:
                content = (subtitles or "") + "\n\n" + (result.get("content", "") or "")
                research_data["project_name"] = result.get("title", "YouTube Video")
                research_data["readme_summary"] = content[:3000]
                llm_result = self._summarize_with_llm(
                    "youtube", research_data["project_name"], content, topic_seed
                )
                if llm_result:
                    research_data.update(llm_result)

        # GitHub - try gh CLI first, fallback to API
        elif "github.com" in hostname:
            owner, repo = self._extract_github_info(topic_seed)
            if owner and repo:
                log.info(f"[Research Agent] Fetching GitHub repo: {owner}/{repo}")
                # Try gh CLI
                gh_result = self._fetch_gh_repo(owner, repo)
                if gh_result:
                    research_data["project_name"] = gh_result.get("title", repo)
                    research_data["readme_summary"] = gh_result.get("content", "")[:3000]
                    llm_result = self._summarize_with_llm(
                        "github", gh_result.get("title", f"{owner}/{repo}"),
                        gh_result.get("content", ""), topic_seed
                    )
                    if llm_result:
                        research_data.update(llm_result)
                else:
                    # Fallback to API
                    repo_data = self._fetch_github_repo(owner, repo)
                    readme = self._fetch_github_readme(owner, repo)
                    key_files = self._fetch_key_files(owner, repo)

                    research_data["project_name"] = repo_data.get("name", repo) if repo_data else repo
                    research_data["readme_summary"] = readme[:3000] if readme else ""

                    llm_result = self._summarize_with_llm(
                        "github", repo_data.get("full_name", f"{owner}/{repo}") if repo_data else f"{owner}/{repo}",
                        readme or repo_data.get("description", "") if repo_data else "",
                        topic_seed,
                        key_files
                    )
                    if llm_result:
                        research_data.update(llm_result)
            else:
                log.info("[Research Agent] Could not parse GitHub URL")

        # HuggingFace
        elif "huggingface.co" in hostname or "hf.co" in hostname:
            log.info(f"[Research Agent] Fetching HuggingFace resource: {topic_seed}")
            api_url = topic_seed.replace("https://huggingface.co", "https://huggingface.co/api")
            if not api_url.endswith("/"):
                api_url += "/"
            resp = self._safe_get(api_url)
            content = resp.json() if resp else {}
            readme_resp = self._safe_get(topic_seed.rstrip("/") + "?format=raw")
            readme = readme_resp.text if readme_resp else ""

            research_data["project_name"] = content.get("modelId") or content.get("id") or "HuggingFace Resource"
            research_data["readme_summary"] = readme[:3000]

            llm_result = self._summarize_with_llm(
                "huggingface", research_data["project_name"], readme, topic_seed
            )
            if llm_result:
                research_data.update(llm_result)

        # Twitter/X
        elif self.twitter_channel.can_handle(topic_seed):
            log.info(f"[Research Agent] Fetching Twitter/X content via Agent-Reach: {topic_seed}")
            # Determine if it's a tweet or an article
            if "/status/" in topic_seed:
                tweet_info = self.twitter_channel.tweet(topic_seed)
                if tweet_info:
                    research_data["project_name"] = f'Tweet by {tweet_info.get("author", "")}'
                    research_data["readme_summary"] = tweet_info.get("text", "")[:3000]
                    llm_result = self._summarize_with_llm(
                        "twitter_tweet", research_data["project_name"], tweet_info.get("text", ""), topic_seed
                    )
                    if llm_result:
                        research_data.update(llm_result)
            elif "/i/events/" in topic_seed or "/read/" in topic_seed:
                article_info = self.twitter_channel.article(topic_seed)
                if article_info:
                    research_data["project_name"] = article_info.get("title", "X Article")
                    research_data["readme_summary"] = article_info.get("text", "")[:3000]
                    llm_result = self._summarize_with_llm(
                        "twitter_article", research_data["project_name"], article_info.get("text", ""), topic_seed
                    )
                    if llm_result:
                        research_data.update(llm_result)
            else:
                log.info(f"[Research Agent] Unhandled Twitter/X URL type: {topic_seed}. Falling back to generic web fetch.")
                # Fallback to generic web fetch for other Twitter URLs (profiles, searches)
                result = self._fetch_web_article(topic_seed)
                if result:
                    research_data["project_name"] = result.get("title", "Twitter/X Page")
                    research_data["readme_summary"] = result.get("content", "")[:3000]
                    llm_result = self._summarize_with_llm(
                        "webpage", research_data["project_name"], result.get("content", ""), topic_seed
                    )
                    if llm_result:
                        research_data.update(llm_result)

        # arXiv
        elif "arxiv.org" in hostname:
            log.info(f"[Research Agent] Fetching arXiv paper: {topic_seed}")
            arxiv_id_match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", topic_seed)
            abstract = ""
            if arxiv_id_match:
                arxiv_id = arxiv_id_match.group(1)
                api_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
                resp = self._safe_get(api_url)
                if resp:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(resp.content)
                    for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
                        summary_elem = entry.find("{http://www.w3.org/2005/Atom}summary")
                        if summary_elem is not None:
                            abstract = summary_elem.text.strip() if summary_elem.text else ""
                        title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                        if title_elem is not None:
                            research_data["project_name"] = title_elem.text.strip()

            research_data["readme_summary"] = abstract[:3000]

            llm_result = self._summarize_with_llm(
                "arxiv", research_data["project_name"], abstract, topic_seed
            )
            if llm_result:
                research_data.update(llm_result)

        # Reddit
        elif self.reddit_channel.can_handle(topic_seed):
            log.info(f"[Research Agent] Fetching Reddit content via Agent-Reach: {topic_seed}")
            # Determine if it's a post or a subreddit
            if "/comments/" in topic_seed:
                post_info = self.reddit_channel.read(topic_seed)
                if post_info:
                    research_data["project_name"] = post_info.get("title", "Reddit Post")
                    research_data["readme_summary"] = post_info.get("text", "")[:3000]
                    llm_result = self._summarize_with_llm(
                        "reddit_post", research_data["project_name"], post_info.get("text", ""), topic_seed
                    )
                    if llm_result:
                        research_data.update(llm_result)
            elif "/r/" in topic_seed:
                subreddit_name = topic_seed.split("/r/")[1].split("/")[0]
                subreddit_info = self.reddit_channel.subreddit(subreddit_name)
                if subreddit_info:
                    research_data["project_name"] = subreddit_info.get("title", f"r/{subreddit_name}")
                    research_data["readme_summary"] = subreddit_info.get("description", "")[:3000]
                    llm_result = self._summarize_with_llm(
                        "reddit_subreddit", research_data["project_name"], subreddit_info.get("description", ""), topic_seed
                    )
                    if llm_result:
                        research_data.update(llm_result)
            else:
                log.info(f"[Research Agent] Unhandled Reddit URL type: {topic_seed}. Falling back to generic web fetch.")
                # Fallback to generic web fetch for other Reddit URLs
                result = self._fetch_web_article(topic_seed)
                if result:
                    research_data["project_name"] = result.get("title", "Reddit Page")
                    research_data["readme_summary"] = result.get("content", "")[:3000]
                    llm_result = self._summarize_with_llm(
                        "webpage", research_data["project_name"], result.get("content", ""), topic_seed
                    )
                    if llm_result:
                        research_data.update(llm_result)

        # Generic web URL - use Jina Reader first, fallback to trafilatura/BS4
        else:
            result = self._fetch_web_article(topic_seed)
            if not result:
                log.info(f"[Research Agent] Jina Reader failed, falling back to trafilatura/BS4: {topic_seed}")
                result = self._fetch_generic_url(topic_seed)

            if result:
                research_data["project_name"] = result.get("title", "Web Resource")
                research_data["readme_summary"] = result.get("content", "")[:3000]

                llm_result = self._summarize_with_llm(
                    "webpage", research_data["project_name"], result.get("content", ""), topic_seed
                )
                if llm_result:
                    research_data.update(llm_result)

        if run_dir:
            research_path = os.path.join(run_dir, "research.json")
            with open(research_path, "w", encoding="utf-8") as f:
                json.dump(research_data, f, indent=2)
            log.info(f"[Research Agent] Saved research to {research_path}")

        return research_data


def research_url(config, url, run_dir=None, workspace_dir=None):
    agent = ResearchAgent(config)
    return agent.run({
        "topic_seed": url,
        "run_dir": run_dir,
        "workspace_dir": workspace_dir
    })

