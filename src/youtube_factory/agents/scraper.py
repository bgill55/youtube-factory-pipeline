import os
import json
import time
import re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

# Optional Playwright support for JavaScript-heavy sites
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


import sys
import builtins

def safe_print(*args, **kwargs):
    try:
        text = " ".join(str(a) for a in args)
        builtins.print(text, **kwargs)
    except Exception:
        pass

print = safe_print


class WebsiteScraper:
    """Crawls a website, extracts article content for video script sourcing."""

    def __init__(self, config):
        self.config = config
        scraper_cfg = config.get("scraper", {})
        self.max_pages = scraper_cfg.get("max_pages", 10)
        self.delay = scraper_cfg.get("delay_seconds", 1.0)
        self.timeout = scraper_cfg.get("timeout_seconds", 15)
        self.user_agent = scraper_cfg.get("user_agent", "Youtube-Factory-Scraper/1.0")
        self.min_word_count = scraper_cfg.get("min_word_count", 50)
        self.allowed_domains = scraper_cfg.get("allowed_domains", [])
        self.use_browser = scraper_cfg.get("use_browser", False)

    def _fetch(self, url):
        """Fetch a URL with timeout and user-agent."""
        headers = {"User-Agent": self.user_agent}
        resp = requests.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp

    def _fetch_with_browser(self, url):
        """Fetch a URL using Playwright for JavaScript rendering."""
        if not HAS_PLAYWRIGHT:
            print("[Scraper] Playwright not installed — falling back to requests")
            return self._fetch(url)
        
        print(f"[Scraper] Using browser to render: {url[:60]}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(self.timeout * 1000)
            try:
                page.goto(url, wait_until="networkidle")
                html = page.content()
            finally:
                browser.close()
        
        # Create a response-like object
        class BrowserResponse:
            def __init__(self, html, url):
                self.text = html
                self.url = url
                self.headers = {"Content-Type": "text/html"}
        return BrowserResponse(html, url)

    def _extract_content(self, url, html):
        """Extract title, body text, and images from HTML."""
        soup = BeautifulSoup(html, "lxml")

        # Remove script, style, nav, footer, header tags
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()

        # Extract title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()

        # Extract main body text
        # Try article or main tag first, fall back to body
        main = soup.find("article") or soup.find("main") or soup.find("body")
        if main:
            paragraphs = main.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre"])
            body_parts = []
            for p in paragraphs:
                text = p.get_text(separator=" ", strip=True)
                if text and len(text) > 10:
                    body_parts.append(text)
            body = "\n\n".join(body_parts)
        else:
            body = soup.get_text(separator="\n", strip=True)

        # Clean body
        body = re.sub(r'\n{3,}', '\n\n', body)
        body = re.sub(r' {2,}', ' ', body)

        # Extract images
        images = []
        if main:
            for img in main.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if src:
                    abs_url = urljoin(url, src)
                    alt = img.get("alt", "")
                    images.append({"url": abs_url, "alt": alt})

        # Extract headings for structure
        headings = []
        if main:
            for h in main.find_all(["h1", "h2", "h3"]):
                text = h.get_text(strip=True)
                if text:
                    headings.append({"level": int(h.name[1]), "text": text})

        word_count = len(body.split())

        return {
            "url": url,
            "title": title,
            "body": body,
            "headings": headings,
            "images": images[:20],  # Cap images
            "word_count": word_count,
        }

    def _extract_links(self, url, html, base_domain):
        """Extract internal links from HTML."""
        soup = BeautifulSoup(html, "lxml")
        links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            abs_url = urljoin(url, href)
            parsed = urlparse(abs_url)
            # Only follow internal links with same domain
            if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
                # Strip fragment
                clean = parsed._replace(fragment="").geturl()
                links.add(clean)
        return links

    def _is_valid_page(self, url):
        """Check if URL is a valid page to scrape (not assets, login pages, etc.)."""
        skip_extensions = {
            ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
            ".pdf", ".zip", ".tar", ".gz", ".mp4", ".mp3", ".wav",
            ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
        }
        parsed = urlparse(url)
        path_lower = parsed.path.lower()
        # Skip file extensions
        if any(path_lower.endswith(ext) for ext in skip_extensions):
            return False
        # Skip common non-content paths
        skip_paths = ["/login", "/register", "/cart", "/checkout", "/admin", "/wp-admin"]
        if any(sp in path_lower for sp in skip_paths):
            return False
        return True

    def scrape(self, url, max_pages=None):
        """Crawl a website starting from the given URL.
        
        Returns dict with 'pages' list and 'summary' info.
        """
        max_pages = max_pages or self.max_pages
        parsed_start = urlparse(url)
        base_domain = parsed_start.netloc

        visited = set()
        queue = [url]
        pages = []
        errors = []

        # Determine if we should use browser rendering
        use_browser = self.use_browser or HAS_PLAYWRIGHT
        print(f"[Scraper] Starting crawl of {url} (max {max_pages} pages, browser={'yes' if use_browser else 'no'})")

        while queue and len(pages) < max_pages:
            current_url = queue.pop(0)

            # Normalize URL
            current_url = current_url.rstrip("/")
            if current_url in visited:
                continue
            visited.add(current_url)

            if not self._is_valid_page(current_url):
                continue

            try:
                print(f"[Scraper] Fetching ({len(pages)+1}/{max_pages}): {current_url[:80]}")
                
                # Try requests first, fall back to browser if thin content
                resp = self._fetch(current_url)
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue

                html = resp.text
                extracted = self._extract_content(current_url, html)

                # If content is thin and browser is available, retry with browser
                if extracted["word_count"] < 200 and use_browser:
                    print(f"[Scraper] Thin content ({extracted['word_count']} words) — retrying with browser...")
                    try:
                        resp = self._fetch_with_browser(current_url)
                        html = resp.text
                        extracted = self._extract_content(current_url, html)
                        print(f"[Scraper] Browser render got {extracted['word_count']} words")
                    except Exception as browser_err:
                        print(f"[Scraper] Browser render failed: {browser_err}")

                # Only keep pages with enough content
                if extracted["word_count"] >= self.min_word_count:
                    pages.append(extracted)
                else:
                    print(f"[Scraper] Skipping {current_url[:60]} ({extracted['word_count']} words < {self.min_word_count})")

                # Find more links to crawl
                links = self._extract_links(current_url, html, base_domain)
                for link in links:
                    if link not in visited:
                        queue.append(link)

            except requests.exceptions.RequestException as e:
                errors.append({"url": current_url, "error": str(e)})
                print(f"[Scraper] Error fetching {current_url[:60]}: {e}")
            except Exception as e:
                errors.append({"url": current_url, "error": str(e)})
                print(f"[Scraper] Parse error on {current_url[:60]}: {e}")

            # Polite delay
            if queue:
                time.sleep(self.delay)

        total_words = sum(p["word_count"] for p in pages)
        print(f"[Scraper] Done. {len(pages)} pages scraped, {total_words} total words, {len(errors)} errors")

        return {
            "pages": pages,
            "total_pages": len(pages),
            "total_words": total_words,
            "base_url": url,
            "errors": errors,
            "summary": self._generate_summary(pages),
        }

    def _generate_summary(self, pages):
        """Generate a brief summary of all scraped content."""
        if not pages:
            return "No content scraped."
        titles = [p["title"] for p in pages if p["title"]]
        if not titles:
            titles = [p["url"].split("/")[-1].replace("-", " ") for p in pages]
        return f"Scraped {len(pages)} pages. Key topics: {'; '.join(titles[:5])}"

    def run(self, inputs):
        """Pipeline interface: load saved scrape or scrape URL and save results."""
        url = inputs.get("url", "")
        workspace_dir = inputs.get("workspace_dir", "D:/Youtube_Factory")
        run_dir = inputs.get("run_dir")
        max_pages = inputs.get("max_pages", self.max_pages)

        # Check if scraped data already exists from dashboard scrape
        shared_path = os.path.join(workspace_dir, "workspace", "scraped_content.json")
        if os.path.exists(shared_path):
            print(f"[Scraper] Loading previously scraped data from {shared_path}")
            with open(shared_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            # Copy to run directory
            if run_dir:
                run_path = os.path.join(run_dir, "scraped_content.json")
                with open(run_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
                result["output_file"] = run_path
            return result

        if not url:
            return {"error": "No URL provided and no scraped data found", "pages": [], "total_pages": 0}

        # No saved data — scrape the site
        result = self.scrape(url, max_pages=max_pages)

        # Save to shared location
        os.makedirs(os.path.dirname(shared_path), exist_ok=True)
        with open(shared_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        # Save to run directory
        if run_dir:
            output_path = os.path.join(run_dir, "scraped_content.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            result["output_file"] = output_path

        return result
