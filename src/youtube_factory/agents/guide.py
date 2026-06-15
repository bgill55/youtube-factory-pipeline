import os
import json
import re
from datetime import datetime
from pipeline.llm_utils import query_llm as _query_llm
from pipeline.prompts import get_system_prompt
from pipeline.guide_deployer import GuideDeployer


class GuideGeneratorAgent:
    """Generates a comprehensive HTML resource guide page for each video."""
    
    def __init__(self, config):
        self.config = config

    def run(self, inputs):
        """Generate guide HTML only (no deployment). Deployment happens after assembly."""
        return self.generate(inputs)

    def generate(self, inputs):
        """Generate guide HTML file."""
        run_dir = inputs.get("run_dir")
        idea_output = inputs.get("idea_output", {})
        script_output = inputs.get("script_output", {})
        research_output = inputs.get("research_output", {})
        
        # Load script content
        script_content = script_output.get("script_content", "")
        if not script_content:
            # Try to load from file
            script_files = [f for f in os.listdir(run_dir) if f.startswith("script_") and f.endswith(".md")]
            if script_files:
                with open(os.path.join(run_dir, script_files[0]), "r", encoding="utf-8") as f:
                    script_content = f.read()
        
        # Extract key data
        selected_topic = idea_output.get("selected_topic", "Video Topic")
        concept_summary = idea_output.get("concept_summary", "")
        keywords = idea_output.get("keywords", [])
        featured_links = idea_output.get("featured_links", [])
        video_goal = idea_output.get("video_goal", "")
        seo_research = idea_output.get("seo_research", {})
        hashtags = idea_output.get("optimized_hashtags", [])
        
        # Use LLM to extract structured guide content from script
        guide_data = self._extract_guide_data(selected_topic, concept_summary, script_content, featured_links)
        
        # Generate HTML guide
        html_content = self._generate_html(
            selected_topic=selected_topic,
            concept_summary=concept_summary,
            video_goal=video_goal,
            keywords=keywords,
            featured_links=featured_links,
            seo_research=seo_research,
            hashtags=hashtags,
            guide_data=guide_data
        )
        
        # Save guide HTML
        guide_path = os.path.join(run_dir, "guide.html")
        with open(guide_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        # Save guide data as JSON for reference
        guide_json_path = os.path.join(run_dir, "guide_data.json")
        with open(guide_json_path, "w", encoding="utf-8") as f:
            json.dump(guide_data, f, indent=2)
        
        print(f"[Guide Generator] Guide page created: {guide_path}")
        
        return {
            "guide_path": guide_path,
            "guide_filename": "guide.html",
            "has_guide": True,
            "guide_url": None  # Not deployed yet
        }

    def deploy(self, inputs):
        """Deploy guide to GitHub Pages. Called after assembly succeeds."""
        run_dir = inputs.get("run_dir")
        guide_output = inputs.get("guide_output", {})
        idea_output = inputs.get("idea_output", {})
        
        guide_path = guide_output.get("guide_path")
        selected_topic = idea_output.get("selected_topic", "guide")
        
        if not guide_path or not os.path.exists(guide_path):
            print("[Guide Deployer] No guide.html found — skipping deployment")
            return {"guide_url": None, "deployed": False}
        
        run_id = os.path.basename(run_dir)
        try:
            deployer = GuideDeployer(self.config)
            guide_url = deployer.deploy_guide(run_dir, run_id, selected_topic)
            if guide_url:
                print(f"[Guide Deployer] Guide deployed to: {guide_url}")
                return {"guide_url": guide_url, "deployed": True}
            return {"guide_url": None, "deployed": False}
        except Exception as e:
            print(f"[Guide Deployer] Deployment failed (non-fatal): {e}")
            return {"guide_url": None, "deployed": False, "error": str(e)}

    def _extract_guide_data(self, topic, summary, script_content, featured_links):
        """Use LLM to extract structured guide data from script content."""
        
        system_prompt = f"""You are a technical content editor creating a comprehensive resource guide.
Given a video script and topic, extract structured data for a detailed guide page.

OUTPUT FORMAT (raw JSON only):
{{
  "overview": "2-3 sentence overview of what this guide covers",
  "key_takeaways": ["Takeaway 1", "Takeaway 2", "Takeaway 3", "Takeaway 4", "Takeaway 5"],
  "prerequisites": ["Prerequisite 1", "Prerequisite 2"],
  "step_by_step": [
    {{"title": "Step 1: Title", "description": "Detailed description of this step", "code_example": "optional code snippet or empty string"}},
    {{"title": "Step 2: Title", "description": "Detailed description", "code_example": ""}}
  ],
  "pro_tips": ["Expert tip 1", "Expert tip 2", "Expert tip 3"],
  "common_pitfalls": ["Pitfall 1: description", "Pitfall 2: description"],
  "tools_mentioned": [
    {{"name": "Tool Name", "description": "What it does", "url": "https://url-if-known.com"}}
  ],
  "further_reading": [
    {{"title": "Resource Title", "url": "https://url.com", "description": "Brief description"}}
  ],
  "faq": [
    {{"question": "Common question?", "answer": "Clear, concise answer"}}
  ]
}}

RULES:
- Extract 5-7 key takeaways from the script
- Create 3-5 step-by-step instructions based on the script's walkthrough
- Include code examples where the script mentions commands or code
- List all tools, repos, or services mentioned
- Generate 3-5 FAQ items a viewer might ask
- Be specific and technical — this is for people who watched the video and want to go deeper

INPUT DATA:
- Topic: {topic}
- Summary: {summary}
- Featured Links: {json.dumps(featured_links)}
- Script Content:
{script_content[:3000]}

OUTPUT: Raw JSON only."""

        try:
            res = _query_llm(self.config, system_prompt, "Extract the guide data now.", task="script", require_json=True)
            clean_text = res.strip()
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                clean_text = "\n".join(lines).strip()
            
            data = json.loads(clean_text)
            return data
        except Exception as e:
            print(f"[Guide Generator] LLM extraction failed: {e}. Using defaults.")
            return self._get_default_guide_data(topic, summary, featured_links)

    def _get_default_guide_data(self, topic, summary, featured_links):
        """Fallback guide data if LLM fails."""
        return {
            "overview": summary or f"A comprehensive guide covering {topic}.",
            "key_takeaways": [
                f"Understanding the core concepts of {topic}",
                "Practical implementation steps",
                "Best practices and optimization tips",
                "Common challenges and how to overcome them",
                "Resources for further learning"
            ],
            "prerequisites": [
                "Basic understanding of the topic",
                "A computer with internet access"
            ],
            "step_by_step": [
                {"title": "Getting Started", "description": "Set up your environment and install necessary tools.", "code_example": ""},
                {"title": "Configuration", "description": "Configure the tools according to your needs.", "code_example": ""},
                {"title": "Implementation", "description": "Follow the steps to implement the solution.", "code_example": ""}
            ],
            "pro_tips": [
                "Start with the basics before diving into advanced features",
                "Keep your configuration backed up",
                "Test in a development environment first"
            ],
            "common_pitfalls": [
                "Skipping the prerequisites can lead to setup issues",
                "Not reading the documentation thoroughly"
            ],
            "tools_mentioned": [
                {"name": link.get("name", "Tool"), "description": "Referenced in the video", "url": link.get("url", "")}
                for link in featured_links
            ],
            "further_reading": [
                {"title": link.get("name", "Resource"), "url": link.get("url", ""), "description": "Official resource"}
                for link in featured_links
            ],
            "faq": [
                {"question": f"What is {topic}?", "answer": summary},
                {"question": "Where can I find more information?", "answer": "Check the links in the description and this guide."}
            ]
        }

    def _generate_html(self, selected_topic, concept_summary, video_goal, keywords, 
                       featured_links, seo_research, hashtags, guide_data):
        """Generate a comprehensive, professional HTML guide page."""
        
        channel = self.config.get("channel", {})
        channel_name = channel.get("name", "Weight and See")
        channel_handle = channel.get("handle", "@WeightnSee")
        channel_tagline = channel.get("tagline", "Your signal through the noise of AI")
        
        # Build sections
        overview = guide_data.get("overview", concept_summary)
        key_takeaways = guide_data.get("key_takeaways", [])
        prerequisites = guide_data.get("prerequisites", [])
        step_by_step = guide_data.get("step_by_step", [])
        pro_tips = guide_data.get("pro_tips", [])
        common_pitfalls = guide_data.get("common_pitfalls", [])
        tools_mentioned = guide_data.get("tools_mentioned", [])
        further_reading = guide_data.get("further_reading", [])
        faq = guide_data.get("faq", [])
        
        # Build HTML
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{concept_summary[:160]}">
    <meta name="keywords" content="{', '.join(keywords[:5])}">
    <meta name="author" content="{channel_name}">
    <meta property="og:title" content="{selected_topic} - Complete Guide">
    <meta property="og:description" content="{concept_summary[:200]}">
    <meta property="og:type" content="article">
    <title>{selected_topic} | {channel_name} Guide</title>
    <style>
        :root {{
            --bg-primary: #0f0f0f;
            --bg-secondary: #1a1a1a;
            --bg-card: #252525;
            --text-primary: #f1f1f1;
            --text-secondary: #a0a0a0;
            --accent: #6366f1;
            --accent-light: #818cf8;
            --green: #22c55e;
            --yellow: #eab308;
            --red: #ef4444;
            --border: #333;
        }}
        
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.7;
        }}
        
        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 40px 24px;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 48px;
            padding-bottom: 32px;
            border-bottom: 1px solid var(--border);
        }}
        
        .badge {{
            display: inline-block;
            background: var(--accent);
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
        }}
        
        h1 {{
            font-size: 2.5rem;
            font-weight: 800;
            margin-bottom: 16px;
            line-height: 1.2;
            background: linear-gradient(135deg, var(--text-primary), var(--accent-light));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .subtitle {{
            font-size: 1.1rem;
            color: var(--text-secondary);
            max-width: 700px;
            margin: 0 auto;
        }}
        
        .meta {{
            display: flex;
            justify-content: center;
            gap: 24px;
            margin-top: 20px;
            font-size: 14px;
            color: var(--text-secondary);
        }}
        
        .section {{
            margin-bottom: 48px;
        }}
        
        .section-title {{
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 1.5rem;
            font-weight: 700;
            margin-bottom: 24px;
            color: var(--accent-light);
        }}
        
        .section-title .icon {{
            font-size: 1.8rem;
        }}
        
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 16px;
        }}
        
        .card h3 {{
            font-size: 1.1rem;
            margin-bottom: 12px;
            color: var(--text-primary);
        }}
        
        .takeaway-list {{
            list-style: none;
        }}
        
        .takeaway-list li {{
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: flex-start;
            gap: 12px;
        }}
        
        .takeaway-list li:last-child {{
            border-bottom: none;
        }}
        
        .takeaway-num {{
            background: var(--accent);
            color: white;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: 700;
            flex-shrink: 0;
        }}
        
        .step {{
            position: relative;
            padding-left: 48px;
            margin-bottom: 32px;
        }}
        
        .step-num {{
            position: absolute;
            left: 0;
            top: 0;
            width: 36px;
            height: 36px;
            background: var(--accent);
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
        }}
        
        .step h3 {{
            font-size: 1.15rem;
            margin-bottom: 8px;
        }}
        
        .step p {{
            color: var(--text-secondary);
        }}
        
        .code-block {{
            background: #0d0d0d;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            margin-top: 12px;
            overflow-x: auto;
            font-family: 'Fira Code', 'Consolas', monospace;
            font-size: 14px;
            color: #a5f3fc;
        }}
        
        .tip {{
            background: rgba(34, 197, 94, 0.1);
            border-left: 4px solid var(--green);
            padding: 16px 20px;
            margin-bottom: 12px;
            border-radius: 0 8px 8px 0;
        }}
        
        .tip strong {{
            color: var(--green);
        }}
        
        .pitfall {{
            background: rgba(239, 68, 68, 0.1);
            border-left: 4px solid var(--red);
            padding: 16px 20px;
            margin-bottom: 12px;
            border-radius: 0 8px 8px 0;
        }}
        
        .pitfall strong {{
            color: var(--red);
        }}
        
        .tool-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 16px;
        }}
        
        .tool-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            transition: transform 0.2s, border-color 0.2s;
        }}
        
        .tool-card:hover {{
            transform: translateY(-2px);
            border-color: var(--accent);
        }}
        
        .tool-card h4 {{
            font-size: 1rem;
            margin-bottom: 8px;
        }}
        
        .tool-card p {{
            font-size: 14px;
            color: var(--text-secondary);
        }}
        
        .tool-card a {{
            display: inline-block;
            margin-top: 12px;
            color: var(--accent-light);
            text-decoration: none;
            font-size: 14px;
        }}
        
        .tool-card a:hover {{
            text-decoration: underline;
        }}
        
        .faq-item {{
            margin-bottom: 20px;
        }}
        
        .faq-item h3 {{
            font-size: 1.05rem;
            margin-bottom: 8px;
            color: var(--yellow);
        }}
        
        .faq-item p {{
            color: var(--text-secondary);
        }}
        
        .footer {{
            margin-top: 64px;
            padding-top: 32px;
            border-top: 1px solid var(--border);
            text-align: center;
            color: var(--text-secondary);
            font-size: 14px;
        }}
        
        .footer a {{
            color: var(--accent-light);
            text-decoration: none;
        }}
        
        .footer a:hover {{
            text-decoration: underline;
        }}
        
        .tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 16px;
            justify-content: center;
        }}
        
        .tag {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 13px;
            color: var(--text-secondary);
        }}
        
        @media (max-width: 600px) {{
            h1 {{ font-size: 1.8rem; }}
            .container {{ padding: 24px 16px; }}
            .meta {{ flex-direction: column; gap: 8px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <span class="badge">Complete Guide</span>
            <h1>{selected_topic}</h1>
            <p class="subtitle">{overview}</p>
            <div class="meta">
                <span>By {channel_name}</span>
                <span>{datetime.now().strftime('%B %d, %Y')}</span>
                <span>{len(step_by_step)} Steps</span>
            </div>
        </header>

        <section class="section">
            <h2 class="section-title"><span class="icon">🎯</span> Key Takeaways</h2>
            <div class="card">
                <ul class="takeaway-list">
"""
        
        for i, takeaway in enumerate(key_takeaways, 1):
            html += f'                    <li><span class="takeaway-num">{i}</span><span>{takeaway}</span></li>\n'
        
        html += """                </ul>
            </div>
        </section>
"""
        
        if prerequisites:
            html += """
        <section class="section">
            <h2 class="section-title"><span class="icon">📋</span> Prerequisites</h2>
            <div class="card">
                <ul>
"""
            for prereq in prerequisites:
                html += f'                    <li style="padding: 6px 0; color: var(--text-secondary);">{prereq}</li>\n'
            html += """                </ul>
            </div>
        </section>
"""
        
        if step_by_step:
            html += """
        <section class="section">
            <h2 class="section-title"><span class="icon">📝</span> Step-by-Step Guide</h2>
"""
            for i, step in enumerate(step_by_step, 1):
                html += f"""
            <div class="step">
                <span class="step-num">{i}</span>
                <h3>{step.get('title', f'Step {i}')}</h3>
                <p>{step.get('description', '')}</p>
"""
                if step.get('code_example'):
                    html += f"""
                <div class="code-block"><pre>{step['code_example']}</pre></div>
"""
                html += """            </div>
"""
            html += """        </section>
"""
        
        if pro_tips:
            html += """
        <section class="section">
            <h2 class="section-title"><span class="icon">💡</span> Pro Tips</h2>
"""
            for tip in pro_tips:
                html += f"""
            <div class="tip">
                <strong>Pro Tip:</strong> {tip}
            </div>
"""
            html += """        </section>
"""
        
        if common_pitfalls:
            html += """
        <section class="section">
            <h2 class="section-title"><span class="icon">⚠️</span> Common Pitfalls</h2>
"""
            for pitfall in common_pitfalls:
                html += f"""
            <div class="pitfall">
                <strong>Watch Out:</strong> {pitfall}
            </div>
"""
            html += """        </section>
"""
        
        if tools_mentioned:
            html += """
        <section class="section">
            <h2 class="section-title"><span class="icon">🛠️</span> Tools & Resources</h2>
            <div class="tool-grid">
"""
            for tool in tools_mentioned:
                html += f"""
                <div class="tool-card">
                    <h4>{tool.get('name', 'Tool')}</h4>
                    <p>{tool.get('description', '')}</p>
                    {f'<a href="{tool["url"]}" target="_blank">View Resource →</a>' if tool.get('url') else ''}
                </div>
"""
            html += """            </div>
        </section>
"""
        
        if faq:
            html += """
        <section class="section">
            <h2 class="section-title"><span class="icon">❓</span> Frequently Asked Questions</h2>
"""
            for item in faq:
                html += f"""
            <div class="faq-item card">
                <h3>{item.get('question', 'Question')}</h3>
                <p>{item.get('answer', 'Answer')}</p>
            </div>
"""
            html += """        </section>
"""
        
        if further_reading:
            html += """
        <section class="section">
            <h2 class="section-title"><span class="icon">📚</span> Further Reading</h2>
            <div class="card">
                <ul>
"""
            for resource in further_reading:
                html += f"""
                    <li style="padding: 8px 0;">
                        <a href="{resource.get('url', '#')}" target="_blank" style="color: var(--accent-light); text-decoration: none;">
                            {resource.get('title', 'Resource')}
                        </a>
                        <span style="color: var(--text-secondary); font-size: 14px;"> — {resource.get('description', '')}</span>
                    </li>
"""
            html += """                </ul>
            </div>
        </section>
"""
        
        html += f"""
        <footer class="footer">
            <p>Created by <strong>{channel_name}</strong> — {channel_tagline}</p>
            <p style="margin-top: 8px;">
                <a href="https://youtube.com/{channel_handle}" target="_blank">Subscribe on YouTube</a>
            </p>
            <div class="tags">
"""
        for tag in hashtags[:5]:
            html += f'                <span class="tag">{tag}</span>\n'
        
        html += f"""            </div>
            <p style="margin-top: 24px; font-size: 12px;">Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} • This guide accompanies the video: "{selected_topic}"</p>
        </footer>
    </div>
</body>
</html>"""
        
        return html
