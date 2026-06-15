import os
import json
import re
import requests
import subprocess
from urllib.parse import urlparse
from pipeline.llm_utils import query_llm as _query_llm
from pipeline.prompts import get_system_prompt


class ResearchAgent:
    def __init__(self, config):
        self.config = config
        self.github_token = config.get("github", {}).get("api_key") or os.getenv("GITHUB_TOKEN")
        self.session = requests.Session()
        if self.github_token:
            self.session.headers.update({"Authorization": f"token {self.github_token}"})
        self.session.headers.update({"User-Agent": "YouTube-Factory-Research-Agent"})
        
        # Ensure gh CLI, mcporter (npm global) are in PATH
        self._gh_path = "/c/Program Files/GitHub CLI"
        self._npm_path = "/c/Users/brica/AppData/Roaming/npm"
        self._env = os.environ.copy()
        paths_to_add = []
        for p in [self._gh_path, self._npm_path]:
            if p and p not in self._env.get("PATH", ""):
                paths_to_add.append(p)
        if paths_to_add:
            self._env["PATH"] = ":".join(paths_to_add) + ":" + self._env["PATH"]

    def _run_cmd(self, cmd, timeout=60, parse_json=False):
        """Run a command and return stdout, optionally parsing as JSON."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env,
                shell=False
            )
            if result.returncode == 0:
                stdout = result.stdout.strip()
                if parse_json and stdout:
                    return json.loads(stdout)
                return stdout
            else:
                print(f"[Research Agent] Command failed: {' '.join(cmd)} - {result.stderr}")
                return None
        except subprocess.TimeoutExpired:
            print(f"[Research Agent] Command timed out: {' '.join(cmd)}")
            return None
        except json.JSONDecodeError as e:
            print(f"[Research Agent] JSON parse error: {e}")
            return None
        except Exception as e:
            print(f"[Research Agent] Command error: {e}")
            return None

    def _fetch_youtube_transcript(self, url):
        """Fetch YouTube transcript using yt-dlp directly."""
        print(f"[Research Agent] Fetching YouTube transcript via yt-dlp: {url}")
        # Get video info and auto-generated subtitles
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
            transcript = ""
            # Try to get auto-generated subtitles
            if result.get("automatic_captions"):
                # yt-dlp doesn't download subs with --print-json, need separate call
                pass
            
            # Get description and title
            return {
                "type": "youtube",
                "title": result.get("title", ""),
                "content": result.get("description", "")[:8000],
                "duration": result.get("duration", 0),
                "channel": result.get("uploader", ""),
                "url": url,
                "video_id": result.get("id", "")
            }
        return None

    def _fetch_youtube_subtitles(self, url):
        """Fetch YouTube subtitles using yt-dlp."""
        print(f"[Research Agent] Fetching YouTube subtitles via yt-dlp: {url}")
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
        """Fetch web article using Jina Reader."""
        print(f"[Research Agent] Fetching web article via Jina Reader: {url}")
        jina_url = f"https://r.jina.ai/{url}"
        result = self._run_cmd(["curl", "-s", "-L", jina_url], timeout=30)
        if result and len(result) > 100:
            return {
                "type": "article",
                "title": "",
                "content": result[:8000],
                "url": url
            }
        return None

    def _search_exa(self, query):
        """Semantic web search using Exa via mcporter MCP."""
        print(f"[Research Agent] Exa search via mcporter: {query}")
        # Use mcporter to call Exa MCP with flag-style args
        cmd = [
            "mcporter.cmd", "--config", "C:\\Users\\brica\\config\\mcporter.json",
            "call", "exa.web_search_exa",
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
        """Fetch RSS feed using feedparser."""
        print(f"[Research Agent] Fetching RSS via feedparser: {url}")
        try:
            import feedparser
            feed = feedparser.parse(url)
            entries = []
            for e in feed.entries[:10]:
                entries.append({
                    "title": e.get("title", ""),
                    "link": e.get("link", ""),
                    "summary": e.get("summary", "")[:500]
                })
            formatted = []
            for e in entries:
                formatted.append(f"Title: {e['title']}\nLink: {e['link']}\nSummary: {e['summary']}")
            return {
                "type": "rss",
                "title": feed.feed.get("title", ""),
                "content": "\n\n---\n\n".join(formatted),
                "entries": entries
            }
        except Exception as e:
            print(f"[Research Agent] feedparser error: {e}")
            return None

    def _safe_get(self, url, **kwargs):
        """Safe GET with timeout and error handling."""
        try:
            resp = self.session.get(url, timeout=15, **kwargs)
            if resp.status_code == 200:
                return resp
        except Exception as e:
            print(f"[Research Agent] Request failed for {url}: {e}")
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
        """Fetch GitHub repo metadata."""
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        resp = self._safe_get(api_url)
        if resp:
            return resp.json()
        return None

    def _fetch_github_readme(self, owner, repo):
        """Fetch README content."""
        paths = ["README.md", "README.rst", "README.txt", "readme.md"]
        for path in paths:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            resp = self._safe_get(api_url, headers={"Accept": "application/vnd.github.v3.raw"})
            if resp and resp.text:
                return resp.text
        return None

    def _fetch_github_contents(self, owner, repo, path=""):
        """Fetch directory contents."""
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        resp = self._safe_get(api_url)
        if resp:
            return resp.json()
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
        """Fetch GitHub repo using gh CLI."""
        print(f"[Research Agent] Fetching GitHub repo via gh CLI: {owner}/{repo}")
        cmd = ["gh", "repo", "view", f"{owner}/{repo}", "--json", "name,description,url,stargazerCount,primaryLanguage"]
        result = self._run_cmd(cmd, timeout=30, parse_json=True)
        if result:
            # Fetch README via gh api
            readme_cmd = ["gh", "api", f"repos/{owner}/{repo}/readme"]
            readme_result = self._run_cmd(readme_cmd, timeout=30, parse_json=True)
            readme = ""
            if readme_result and readme_result.get("content"):
                import base64
                readme = base64.b64decode(readme_result["content"]).decode("utf-8", errors="ignore")
            return {
                "type": "github",
                "title": result.get("name", ""),
                "content": readme[:8000] or result.get("description", ""),
                "url": result.get("url", ""),
                "stars": result.get("stargazerCount", 0),
                "language": result.get("primaryLanguage", {}).get("name", "") if result.get("primaryLanguage") else ""
            }
        return None

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
            print(f"[Research Agent] Trafilatura failed: {e}")

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
            return json.loads(clean)
        except Exception as e:
            print(f"[Research Agent] LLM summarization failed: {e}")
            return None

    def run(self, inputs):
        """Main entry point."""
        topic_seed = inputs.get("topic_seed", "").strip()
        run_dir = inputs.get("run_dir")
        workspace_dir = inputs.get("workspace_dir")

        url_match = re.match(r"^https?://", topic_seed)
        if not url_match:
            return {
                "source_url": "",
                "project_name": "",
                "one_liner": "",
                "tech_stack": [],
                "key_features": [],
                "innovation_hooks": [],
                "demo_ideas": [],
                "code_snippets": [],
                "architecture_notes": "",
                "readme_summary": "",
                "is_url_source": False
            }

        print(f"[Research Agent] Analyzing URL: {topic_seed}")

        parsed = urlparse(topic_seed)
        hostname = parsed.netloc.lower()

        research_data = {
            "source_url": topic_seed,
            "project_name": "",
            "one_liner": "",
            "tech_stack": [],
            "key_features": [],
            "innovation_hooks": [],
            "demo_ideas": [],
            "code_snippets": [],
            "architecture_notes": "",
            "readme_summary": "",
            "is_url_source": True
        }

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
                print(f"[Research Agent] Fetching GitHub repo: {owner}/{repo}")
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
                print("[Research Agent] Could not parse GitHub URL")

        # HuggingFace
        elif "huggingface.co" in hostname or "hf.co" in hostname:
            print(f"[Research Agent] Fetching HuggingFace resource: {topic_seed}")
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

        # arXiv
        elif "arxiv.org" in hostname:
            print(f"[Research Agent] Fetching arXiv paper: {topic_seed}")
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

        # Generic web URL - use Jina Reader first, fallback to trafilatura/BS4
        else:
            result = self._fetch_web_article(topic_seed)
            if not result:
                print(f"[Research Agent] Jina Reader failed, falling back to trafilatura/BS4: {topic_seed}")
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
            print(f"[Research Agent] Saved research to {research_path}")

        return research_data


def research_url(config, url, run_dir=None, workspace_dir=None):
    agent = ResearchAgent(config)
    return agent.run({
        "topic_seed": url,
        "run_dir": run_dir,
        "workspace_dir": workspace_dir
    })