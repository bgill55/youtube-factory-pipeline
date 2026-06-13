import os
import json
import re
import requests
from urllib.parse import urlparse
from youtube_factory.llm import query_llm as _query_llm
from youtube_factory.prompts import get_system_prompt

class ResearchAgent:
    def __init__(self, config):
        self.config = config
        self.github_token = config.get("github", {}).get("api_key") or os.getenv("GITHUB_TOKEN")
        self.session = requests.Session()
        if self.github_token:
            self.session.headers.update({"Authorization": f"token {self.github_token}"})
        self.session.headers.update({"User-Agent": "YouTube-Factory-Research-Agent"})

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

        if "github.com" in hostname:
            owner, repo = self._extract_github_info(topic_seed)
            if owner and repo:
                print(f"[Research Agent] Fetching GitHub repo: {owner}/{repo}")
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

        else:
            print(f"[Research Agent] Fetching generic URL: {topic_seed}")
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