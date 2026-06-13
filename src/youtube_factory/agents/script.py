import os
import json
import re
import random
from youtube_factory.llm import query_llm as _query_llm
from youtube_factory.prompts import get_system_prompt

def _safe_print(msg):
    """Print that handles non-encodable characters on Windows cp1252 console."""
    print(msg.encode("cp1252", errors="replace").decode("cp1252"))

class ScriptwriterAgent:
    ANALOGY_THEMES = [
        "a retro 1980s arcade machine handling tokens, high scores, and ticket dispensers",
        "a honeybee colony with scouts, workers, and a queen delegating pollen tasks",
        "a plumbing system in a skyscraper with safety valves, filters, and high-pressure pumps",
        "a local post office where sorting clerk cats physically distribute mail to cubby holes",
        "dog agility training where a handler guides a dog through hoops, tunnels, and see-saws",
        "brewing a complex triple-shot espresso on a manual lever machine with pressure gauges",
        "a high-speed train coupling and decoupling coaches mid-journey to optimize routes",
        "a theatrical stage production where stagehands shift props behind a curtain in 5 seconds",
        "a 90s cassette mixtape swap meet with custom hand-written labels and track lengths",
        "a medieval castle defense with archers, scouts, and messengers navigating narrow drawbridges",
        "a busy harbor dock where tugboats guide giant container ships into precise berths",
        "an old-school library card catalog system run by hyper-organized squirrels",
        "a Formula 1 pit stop crew changing tires and adjusting wings in under 2 seconds",
        "a retro pneumatic tube mail network shooting capsules through office building walls",
        "a botanical garden irrigation system balancing moisture for tropical vs desert plants",
        "a vintage mechanical clockwork mechanism with brass gears, escapements, and weights",
        "a massive logistics warehouse where forklift drivers organize packages by weight",
        "a local bakery conveyor belt system separating glazed, filled, and plain donuts",
        "a custom bicycle assembly shop fitting chains, gears, and brakes for different terrains",
        "a busy airport control tower directing takeoffs, landings, and taxiway sequences",
        "a classic typewriter mechanism where keys strike ribbon and carriage advances",
        "a pinball machine with bumpers, flippers, ramps, and multi-ball triggers",
        "an assembly line of workers putting together a complex clock with fine tweezers",
        "a subway transit system where trains periodically sync up to swap passengers",
        "a retro synthesizer modular patch bay where cables connect filters and oscillators",
        "a greenhouse venting system opening and closing panels based on humidity sensors",
        "a theatrical ventriloquist act where a performer controls a puppet's expressions",
        "a vending machine sorting coins by size and returning exact change",
        "a toll booth plaza sorting cars, trucks, and motorcycles into correct lanes",
        "a spinning loom weaving colored threads into complex geometric tapestry patterns"
    ]

    def __init__(self, config):
        self.config = config

    def query_llm(self, system_prompt, user_prompt):
        return _query_llm(self.config, system_prompt, user_prompt, task="script")

    def run(self, inputs):
        idea_output = inputs.get("idea_output")
        scraped_content = inputs.get("scraped_content")
        run_dir = inputs.get("run_dir")

        selected_topic = idea_output.get("selected_topic")
        concept_summary = idea_output.get("concept_summary")
        keywords = idea_output.get("keywords", [])
        video_goal = idea_output.get("video_goal")

        # Load channel identity from config
        channel = self.config.get("channel", {})
        channel_name = channel.get("name", "the channel")
        channel_handle = channel.get("handle", "")
        channel_tagline = channel.get("tagline", "")
        channel_niche = channel.get("niche", "")

        # Format scraped content as source material for the script
        scraped_source_material = ""
        if scraped_content and isinstance(scraped_content, dict):
            pages = scraped_content.get("pages", [])
            # Filter out low-value pages
            skip_keywords = ["terms of service", "privacy policy", "terms of use",
                             "cookie", "legal", "login", "sign up", "register",
                             "contact us", "about us", "faq"]
            useful_pages = []
            for p in pages:
                title_lower = (p.get("title") or "").lower()
                body_lower = (p.get("body") or "")[:200].lower()
                if not any(kw in title_lower or kw in body_lower for kw in skip_keywords):
                    if p.get("word_count", 0) >= 50:
                        useful_pages.append(p)

            if useful_pages:
                scraped_parts = ["\n\nSOURCE MATERIAL (use this as the basis for your script — quote facts, cite details, reference specific features):"]
                for i, page in enumerate(useful_pages[:5]):
                    title = page.get("title", "Untitled")
                    body = page.get("body", "")[:1500]
                    url = page.get("url", "")
                    scraped_parts.append(f"\n--- Article {i+1}: {title} ---")
                    scraped_parts.append(f"URL: {url}")
                    scraped_parts.append(body)
                scraped_source_material = "\n".join(scraped_parts)

        # Salt Injection — generate a FRESH analogy for this specific topic
        try:
            example_analogies = "\n".join(f"- {t}" for t in random.sample(self.ANALOGY_THEMES, min(5, len(self.ANALOGY_THEMES))))
            salt_prompt = (
                f"Video topic: {selected_topic}\n"
                f"Concept: {concept_summary[:200]}\n\n"
                f"Generate ONE completely fresh, unexpected analogy to explain this topic to a non-technical audience. "
                f"The analogy must be:\n"
                f"- Something MOST people have experienced or can visualize\n"
                f"- Unexpected and specific (not generic like 'cooking' or 'building')\n"
                f"- Capable of mapping to multiple aspects of the topic (input→output, scaling, bottlenecks, etc.)\n"
                f"- Different from these examples (which have been overused): {example_analogies}\n\n"
                f"Return ONLY the analogy as a single sentence. No quotes, no explanation.\n"
                f"Example good output: 'a postal worker sorting letters during a holiday rush'"
            )
            selected_theme = self.query_llm("Generate a fresh analogy.", salt_prompt).strip().strip('"').strip("'")
            if len(selected_theme) < 10:
                selected_theme = random.choice(self.ANALOGY_THEMES)
        except Exception:
            selected_theme = random.choice(self.ANALOGY_THEMES)
        safe_theme = selected_theme.encode("cp1252", errors="replace").decode("cp1252")
        _safe_print(f"[Scriptwriter] Salt Injection: Injecting analogy theme -> '{safe_theme}'")
        
        analogy_theme_instruction = (
            f"CRITICAL CREATIVE CONSTRAINT: You are strictly forbidden from using generic clichés (such as 'chefs in a kitchen', 'orchestrating an orchestra', 'building blocks', or 'assembling a puzzle'). "
            f"Instead, you MUST explain the core technical concepts of the model/tool using the following fresh, creative analogy theme: '{selected_theme}'. "
            f"Ground your explanation in this theme, weaving it throughout the script to make the complex technology feel tangible and uniquely memorable."
        )

        # Build system prompt from centralized registry with dynamic data injection
        system_prompt = get_system_prompt(
            "script",
            channel_name=channel_name,
            channel_handle=channel_handle,
            channel_tagline=channel_tagline,
            channel_niche=channel_niche,
            selected_topic=selected_topic,
            concept_summary=concept_summary,
            video_goal=video_goal,
            keywords=", ".join(keywords),
            scraped_source_material=scraped_source_material,
            analogy_theme_instruction=analogy_theme_instruction
        )

        # Simple user prompt - the dynamic data is in the system prompt
        user_prompt = "Write the complete video script now."

        script_markdown = self.query_llm(system_prompt, user_prompt)

        # === SOURCE VERIFICATION ===
        # Cross-validate: check if script contains specific numbers/benchmarks
        # not found in the source material (common hallucination pattern)
        unverified_claims = []
        if scraped_content and isinstance(scraped_content, dict):
            source_text = " ".join(
                p.get("body", "") for p in scraped_content.get("pages", [])
            ).lower()
            # Find specific claims in script (numbers with units)
            claims = re.findall(
                r'\b\d+[\.,]?\d*\s*(?:tokens?\/?s(?:ec)?|fps|gb|mb|tb|ms|seconds?|minutes?|%|percent)\b',
                script_markdown.lower()
            )
            for claim in claims:
                if claim.strip() not in source_text:
                    print(f"[Scriptwriter] WARNING: Script mentions '{claim}' but it's not in source material — may be hallucinated")
                    unverified_claims.append(claim)

        # LLM-powered claim verification: check each factual statement against sources
        if scraped_content and isinstance(scraped_content, dict) and unverified_claims:
            source_excerpts = []
            for p in scraped_content.get("pages", [])[:3]:
                source_excerpts.append(f"- {p.get('title', '')}: {p.get('body', '')[:500]}")
            source_context = "\n".join(source_excerpts)

            verify_prompt = f"""You are a fact-checker. Compare these claims from a video script against the source material.
For each claim, respond with VERIFIED (found in source) or UNVERIFIED (not found / possibly hallucinated).

Claims to check:
{json.dumps(unverified_claims[:10])}

Source material:
{source_context}

Output JSON: {{"results": [{{"claim": "...", "status": "VERIFIED" or "UNVERIFIED", "reason": "brief explanation"}}]}}"""

            try:
                verify_result = _query_llm(self.config, "You are a meticulous fact-checker.", verify_prompt, task="script", require_json=True)
                verify_data = json.loads(verify_result.strip().strip('`').replace('```json', '').replace('```', ''))
                for item in verify_data.get("results", []):
                    if item.get("status") == "UNVERIFIED":
                        print(f"[Scriptwriter] UNVERIFIED CLAIM: '{item.get('claim')}' — {item.get('reason', 'Not found in source')}")
            except Exception as e:
                print(f"[Scriptwriter] Claim verification failed: {e}")

        # Add source citations footer to script
        citations = []
        if scraped_content and isinstance(scraped_content, dict):
            for p in scraped_content.get("pages", [])[:5]:
                url = p.get("url", "")
                title = p.get("title", "")
                if url and title:
                    citations.append(f"- {title}: {url}")
        if citations:
            script_markdown += "\n\n---\n**Sources:**\n" + "\n".join(citations)

        # Sanitize topic name for filename
        safe_topic = re.sub(r'[^a-zA-Z0-9]', '_', selected_topic).strip('_').lower()
        # Keep filename short
        safe_topic = safe_topic[:30]
        script_file_name = f"script_{safe_topic}.md"
        script_file_path = os.path.join(run_dir, script_file_name)

        with open(script_file_path, "w", encoding="utf-8") as f:
            f.write(script_markdown)

        return {
            "script_file": script_file_path,
            "script_text": script_markdown
        }
