import os, json, re
from datetime import datetime
from youtube_factory.llm import query_llm
from youtube_factory.logging_utils import get_logger

log = get_logger("agent_self_critique")

SELF_CRITIQUE_PROMPT = """You are a YouTube quality auditor. Review the following video production and provide a candid critique.

Rate each dimension 1-10 and give a 1-sentence improvement suggestion:

1. TITLE CTR POTENTIAL: Is the title clickable? Does it create curiosity or urgency?
2. THUMBNAIL EFFECTIVENESS: Based on the title and topic, how well will the thumbnail convert?
3. TOPIC TIMELINESS: Is this topic currently trending or evergreen?
4. SCRIPT QUALITY: Is the hook strong? Does it deliver on the promise?
5. SEO POTENTIAL: Does the target audience search for this?
6. OVERALL PRODUCTION VALUE: Considering the full pipeline output.

Output as JSON:
{
  "scores": {
    "title_ctr": {"score": 0, "suggestion": ""},
    "thumbnail": {"score": 0, "suggestion": ""},
    "timeliness": {"score": 0, "suggestion": ""},
    "script_quality": {"score": 0, "suggestion": ""},
    "seo_potential": {"score": 0, "suggestion": ""},
    "overall": {"score": 0, "suggestion": ""}
  },
  "final_grade": "A/B/C/D/F",
  "summary": "2-3 sentence overall assessment",
  "one_thing_to_improve": "The single highest-impact change for next time"
}
"""


class SelfCritiqueAgent:
    def __init__(self, config):
        self.config = config

    def run(self, inputs):
        run_dir = inputs.get("run_dir", "")
        idea_output = inputs.get("idea_output", {}) or {}
        script_output = inputs.get("script_output", {}) or {}
        upload_output = inputs.get("upload_output", {}) or {}
        guide_output = inputs.get("guide_output", {}) or {}

        topic = ""
        if isinstance(idea_output, dict):
            topic = idea_output.get("selected_topic") or idea_output.get("topic_seed", "")
        elif isinstance(idea_output, str):
            topic = idea_output

        summary = ""
        if isinstance(idea_output, dict):
            summary = idea_output.get("concept_summary", "")

        script_text = ""
        if isinstance(script_output, dict):
            script_text = script_output.get("script_text", "") or script_output.get("text", "")
        elif isinstance(script_output, str):
            script_text = script_output

        video_url = ""
        if isinstance(upload_output, dict):
            video_url = upload_output.get("video_url", "")

        guide_url = ""
        if isinstance(guide_output, dict):
            guide_url = guide_output.get("guide_url", "") or guide_output.get("deploy_url", "")

        if not topic:
            log.warning("[SelfCritique] No topic found, skipping critique.")
            return {"status": "SKIPPED", "reason": "No topic to critique"}

        user_prompt = f"""
Title: {topic}
Summary: {summary}

Script Preview (first 500 chars):
{script_text[:500]}

Video URL: {video_url}
Guide URL: {guide_url}

Critique this video production and provide improvement recommendations.
"""

        try:
            response = query_llm(self.config, SELF_CRITIQUE_PROMPT, user_prompt, task="default", require_json=True)
            clean = response.strip()
            if clean.startswith("```"):
                lines = clean.splitlines()
                if lines[0].startswith("```"): lines = lines[1:]
                if lines[-1].startswith("```"): lines = lines[:-1]
                clean = "\n".join(lines).strip()
            clean = re.sub(r',\s*}', '}', clean)
            clean = re.sub(r',\s*\]', ']', clean)
            critique = json.loads(clean)
        except Exception as e:
            log.warning(f"[SelfCritique] LLM critique failed: {e}")
            critique = {"error": str(e), "final_grade": "N/A", "summary": "Critique generation failed.", "scores": {}}

        critique["topic"] = topic
        critique["fetched_at"] = datetime.now().isoformat()

        critique_path = os.path.join(run_dir, "self_critique.json")
        with open(critique_path, "w", encoding="utf-8") as f:
            json.dump(critique, f, indent=2, ensure_ascii=False)

        log.info(f"[SelfCritique] Grade: {critique.get('final_grade', 'N/A')} — {critique.get('summary', '')[:100]}")
        return {"status": "SUCCESS", "critique": critique}
