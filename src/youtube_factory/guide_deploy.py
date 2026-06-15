import os
import json
import subprocess
import shutil
from datetime import datetime


class GuideDeployer:
    """Deploys guide pages to GitHub Pages."""
    
    def __init__(self, config):
        self.config = config
        self.deploy_config = config.get("guide_deploy", {})
        self.repo_name = self.deploy_config.get("repo_name", "weightandsee-guides")
        self.branch = self.deploy_config.get("branch", "Master")
        self.username = self.deploy_config.get("username", "bgill55")
        self.guides_dir = self.deploy_config.get("guides_dir", "guides")
        
    def deploy_guide(self, run_dir, run_id, title="guide"):
        """Deploy a guide HTML file to GitHub Pages."""
        guide_path = os.path.join(run_dir, "guide.html")
        if not os.path.exists(guide_path):
            print(f"[Guide Deployer] No guide.html found in {run_dir}")
            return None
        
        # Create a URL-friendly slug from the title
        slug = self._title_to_slug(title)
        
        # Clone or pull the guides repo
        repo_dir = os.path.join(run_dir, "_guides_repo")
        repo_url = f"https://github.com/{self.username}/{self.repo_name}.git"
        
        try:
            if os.path.exists(os.path.join(repo_dir, ".git")):
                # Pull latest
                result = subprocess.run(["git", "-C", repo_dir, "pull"], 
                             capture_output=True, text=True)
                if result.returncode != 0:
                    # Stale clone — remove and re-clone
                    print(f"[Guide Deployer] Stale clone detected, re-cloning...")
                    shutil.rmtree(repo_dir, ignore_errors=True)
                    subprocess.run(["git", "clone", repo_url, repo_dir], 
                                 capture_output=True, text=True, check=True)
            else:
                # Remove any leftover non-git directory
                if os.path.exists(repo_dir):
                    shutil.rmtree(repo_dir, ignore_errors=True)
                # Clone
                subprocess.run(["git", "clone", repo_url, repo_dir], 
                             capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[Guide Deployer] Git clone/pull failed: {e}")
            return None
        
        # Create .nojekyll to prevent Jekyll processing
        nojekyll_path = os.path.join(repo_dir, ".nojekyll")
        if not os.path.exists(nojekyll_path):
            with open(nojekyll_path, "w") as f:
                pass
        
        # Create guides directory if it doesn't exist
        guides_path = os.path.join(repo_dir, self.guides_dir)
        os.makedirs(guides_path, exist_ok=True)
        
        # Duplicate check: skip if guide with same title already exists
        existing_slugs = set()
        for item in os.listdir(guides_path):
            item_path = os.path.join(guides_path, item)
            if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, "index.html")):
                existing_slugs.add(item)
        
        if slug in existing_slugs:
            print(f"[Guide Deployer] Guide '{title}' already exists at /{slug}/ — skipping deploy")
            public_url = f"https://{self.username}.github.io/{self.repo_name}/{self.guides_dir}/{slug}"
            shutil.rmtree(repo_dir, ignore_errors=True)
            return public_url
        
        # Create run-specific directory using title slug
        run_guide_dir = os.path.join(guides_path, slug)
        os.makedirs(run_guide_dir, exist_ok=True)
        
        # Copy guide.html
        dst = os.path.join(run_guide_dir, "index.html")
        shutil.copy2(guide_path, dst)
        
        # Copy any additional assets (CSS, images, etc.)
        for f in os.listdir(run_dir):
            if f.endswith(('.css', '.js', '.png', '.jpg', '.gif')):
                src = os.path.join(run_dir, f)
                shutil.copy2(src, os.path.join(run_guide_dir, f))
        
        # Regenerate categorized README
        self._regenerate_readme(repo_dir)
        
        # Create an index page listing all guides
        self._update_index_page(guides_path)
        
        # Also create root index.html if it doesn't exist (so GitHub Pages shows guides, not README)
        root_index = os.path.join(repo_dir, "index.html")
        if not os.path.exists(root_index):
            with open(root_index, "w", encoding="utf-8") as f:
                f.write(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0; url=./{self.guides_dir}/">
    <title>Weight and See Guides</title>
</head>
<body>
    <p>Redirecting to <a href="./{self.guides_dir}/">guides</a>...</p>
</body>
</html>""")
        
        # Commit and push
        try:
            subprocess.run(["git", "-C", repo_dir, "add", "."], 
                         capture_output=True, text=True, check=True)
            subprocess.run(["git", "-C", repo_dir, "commit", "-m", f"Add guide: {slug}"], 
                         capture_output=True, text=True, check=True)
            subprocess.run(["git", "-C", repo_dir, "push"], 
                         capture_output=True, text=True, check=True)
            
            # Generate public URL
            public_url = f"https://{self.username}.github.io/{self.repo_name}/{self.guides_dir}/{slug}"
            print(f"[Guide Deployer] Guide deployed to: {public_url}")
            
            # Clean up repo clone
            shutil.rmtree(repo_dir, ignore_errors=True)
            
            return public_url
        except subprocess.CalledProcessError as e:
            print(f"[Guide Deployer] Git push failed: {e}")
            shutil.rmtree(repo_dir, ignore_errors=True)
            return None
    
    def _update_index_page(self, guides_path):
        """Create/update an index page listing all guides."""
        index_path = os.path.join(guides_path, "index.html")
        
        # Scan for all guide directories
        guides = []
        seen_titles = set()
        for item in sorted(os.listdir(guides_path)):
            item_path = os.path.join(guides_path, item)
            if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, "index.html")):
                # Try to read the title from the guide
                title = item.replace("-", " ").title()
                try:
                    with open(os.path.join(item_path, "index.html"), "r", encoding="utf-8") as f:
                        content = f.read()
                        # Extract title from <title> tag
                        if "<title>" in content:
                            start = content.find("<title>") + 7
                            end = content.find("</title>")
                            if end > start:
                                title = content[start:end].split("|")[0].strip()
                except:
                    pass
                # Deduplicate by title (keep longest slug)
                if title not in seen_titles:
                    seen_titles.add(title)
                    guides.append({"id": item, "title": title, "path": item})
                else:
                    # Replace with longer slug if this one is longer
                    for i, g in enumerate(guides):
                        if g["title"] == title and len(item) > len(g["id"]):
                            guides[i] = {"id": item, "title": title, "path": item}
                            break
        
        # Generate index HTML
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Weight and See - Video Guides</title>
    <style>
        :root {
            --bg: #0f0f0f;
            --card: #1a1a1a;
            --text: #f1f1f1;
            --text-secondary: #a0a0a0;
            --accent: #6366f1;
            --border: #333;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 40px 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        h1 {
            font-size: 2rem;
            margin-bottom: 8px;
            background: linear-gradient(135deg, var(--text), var(--accent));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .subtitle { color: var(--text-secondary); margin-bottom: 40px; }
        .guide-list { list-style: none; }
        .guide-item {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            margin-bottom: 12px;
            transition: transform 0.2s, border-color 0.2s;
        }
        .guide-item:hover {
            transform: translateY(-2px);
            border-color: var(--accent);
        }
        .guide-item a {
            display: block;
            padding: 20px;
            text-decoration: none;
            color: var(--text);
        }
        .guide-item h3 { font-size: 1.1rem; margin-bottom: 4px; }
        .guide-item p { font-size: 14px; color: var(--text-secondary); }
        .empty { color: var(--text-secondary); text-align: center; padding: 60px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Weight and See</h1>
        <p class="subtitle">Video Guides & Resources</p>
"""
        
        if guides:
            html += '        <ul class="guide-list">\n'
            for g in reversed(guides):  # Newest first
                html += f"""            <li class="guide-item">
                <a href="./{g['id']}/">
                    <h3>{g['title']}</h3>
                    <p>View guide →</p>
                </a>
            </li>
"""
            html += "        </ul>\n"
        else:
            html += '        <p class="empty">No guides published yet.</p>\n'
        
        html += """    </div>
</body>
</html>"""
        
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(html)
    
    def _regenerate_readme(self, repo_dir):
        """Regenerate the categorized README.md from guide folders."""
        generate_script = os.path.join(repo_dir, "generate_readme.py")
        pipeline_generate_script = os.path.join(os.path.dirname(__file__), "generate_readme.py")
        
        # Always copy/overwrite generate_readme.py from pipeline to keep it updated in repo
        if os.path.exists(pipeline_generate_script):
            shutil.copy2(pipeline_generate_script, generate_script)
            print(f"[Guide Deployer] Copied/updated generate_readme.py in repo")
        
        if not os.path.exists(generate_script):
            print(f"[Guide Deployer] generate_readme.py not found — skipping README update")
            return
        
        try:
            result = subprocess.run(
                ["python", generate_script],
                capture_output=True, text=True, cwd=repo_dir,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}
            )
            if result.returncode == 0:
                print(f"[Guide Deployer] README.md regenerated successfully")
            else:
                print(f"[Guide Deployer] README generation warning: {result.stderr[:200]}")
        except Exception as e:
            print(f"[Guide Deployer] README generation failed: {e}")
    
    def _title_to_slug(self, title):
        """Convert a video title to a URL-friendly slug."""
        import re
        # Lowercase
        slug = title.lower()
        # Remove special characters, keep alphanumeric and spaces
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        # Replace spaces with hyphens
        slug = re.sub(r'\s+', '-', slug.strip())
        # Remove multiple hyphens
        slug = re.sub(r'-+', '-', slug)
        # Truncate to 80 chars (longer for better readability)
        slug = slug[:80].rstrip('-')
        return slug
