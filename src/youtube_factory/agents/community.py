from youtube_factory.logging_utils import get_logger

log = get_logger("agent_community")
import os
import json
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from youtube_factory.llm import query_llm as _query_llm


class CommunityPostAgent:
    """Generates and publishes YouTube Community posts for video promotion."""
    
    def __init__(self, config):
        self.config = config
        self.scopes = [
            "https://www.googleapis.com/auth/youtube.force-ssl"
        ]

    def run(self, inputs):
        run_dir = inputs.get("run_dir")
        idea_output = inputs.get("idea_output", {})
        visual_output = inputs.get("visual_output", {})
        action = inputs.get("action", "generate")  # "generate", "poll", "post"
        
        selected_topic = idea_output.get("selected_topic", "Video Topic")
        concept_summary = idea_output.get("concept_summary", "")
        keywords = idea_output.get("keywords", [])
        
        # Generate post variations
        posts = self._generate_posts(selected_topic, concept_summary, keywords, visual_output)
        
        # Save to run directory
        posts_path = os.path.join(run_dir, "community_posts.json")
        with open(posts_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, indent=2)
        
        log.info(f"[Community Post] Generated {len(posts)} post variations")
        
        # If action is "post", publish the selected one
        published_url = None
        if action == "post":
            post_index = inputs.get("post_index", 0)
            if post_index < len(posts):
                published_url = self._publish_post(posts[post_index], run_dir)
        
        return {
            "posts": posts,
            "has_posts": True,
            "published_url": published_url
        }

    def _generate_posts(self, topic, summary, keywords, visual_output):
        """Generate different community post variations."""
        posts = []
        
        # Get thumbnail variants for polls
        thumbnail_meta = visual_output.get("thumbnail_metadata", {})
        variants = thumbnail_meta.get("variants", [])
        title_suggestions = thumbnail_meta.get("title_suggestions", [])
        
        # 1. Teaser post (hype the video)
        teaser = self._generate_teaser(topic, summary)
        posts.append({
            "type": "teaser",
            "text": teaser,
            "image": "thumbnail.jpg",
            "description": "Pre-video hype post"
        })
        
        # 2. Thumbnail poll (A/B test)
        if len(variants) >= 2:
            poll = self._generate_thumbnail_poll(topic, variants)
            posts.append(poll)
        
        # 3. Question post (engagement)
        question = self._generate_question(topic, keywords)
        posts.append({
            "type": "question",
            "text": question,
            "options": [],
            "description": "Community engagement question"
        })
        
        # 4. Post-video summary
        summary_post = self._generate_summary_post(topic, summary, keywords)
        posts.append({
            "type": "summary",
            "text": summary_post,
            "image": "thumbnail.jpg",
            "description": "Post-video wrap-up with guide link"
        })
        
        return posts

    def _generate_teaser(self, topic, summary):
        """Generate a teaser/hype post for an upcoming video."""
        system_prompt = f"""You are a YouTube community manager creating hype posts for the "Weight and See" channel.
Create a short, engaging community post teasing an upcoming video.

RULES:
- 2-3 sentences max
- Create curiosity, don't reveal everything
- Use emojis sparingly (2-3 max)
- End with a call to action (turn on notifications, guess the topic, etc.)
- NO hashtags in the post text (they look spammy on community posts)
- Tone: exciting, insider-knowledge, slightly mysterious

VIDEO TOPIC: {topic}
VIDEO SUMMARY: {summary}

OUTPUT: Just the post text. No quotes, no explanation."""

        try:
            res = _query_llm(self.config, system_prompt, "Generate the teaser post.", task="script")
            return res.strip()
        except Exception as e:
            log.info(f"[Community Post] Teaser generation failed: {e}")
            return f"Something big is coming to the channel... 🤫\n\nCan you guess what we're covering next?\n\nDrop your guesses below! 👇"

    def _generate_thumbnail_poll(self, topic, variants):
        """Generate a thumbnail A/B test poll."""
        # Get text overlays from variants
        options = []
        for v in variants[:3]:
            text = v.get("text_overlay", "Option")
            options.append(text)
        
        # Create poll question
        poll_text = f"Which thumbnail would make YOU click? 🎨\n\nTesting some new styles for the upcoming video on {topic[:50]}..."
        
        return {
            "type": "poll",
            "text": poll_text,
            "options": options,
            "image": "thumbnail_a.jpg",
            "description": "Thumbnail A/B test poll"
        }

    def _generate_question(self, topic, keywords):
        """Generate an engagement question post."""
        system_prompt = f"""You are a YouTube community manager creating engagement posts for the "Weight and See" channel.
Create a question post that encourages comments and discussion.

RULES:
- 1-2 sentences for the question
- Make it personally relevant to viewers
- Easy to answer (one-line replies)
- Related to the video topic but not a spoiler
- Use 1-2 emojis max

VIDEO TOPIC: {topic}
KEYWORDS: {', '.join(keywords[:3])}

OUTPUT: Just the question text. No quotes, no explanation."""

        try:
            res = _query_llm(self.config, system_prompt, "Generate the question post.", task="script")
            return res.strip()
        except Exception as e:
            log.info(f"[Community Post] Question generation failed: {e}")
            return f"Quick question for the community:\n\nWhat's your biggest struggle with {keywords[0] if keywords else 'AI'} right now?\n\nDrop a comment below - might cover it in the next video! 💬"

    def _generate_summary_post(self, topic, summary, keywords):
        """Generate a post-video summary post with guide link."""
        system_prompt = f"""You are a YouTube community manager creating post-video wrap-up posts for the "Weight and See" channel.
Create a summary post that recaps the video and links to the written guide.

RULES:
- 3-4 sentences max
- Highlight 2-3 key takeaways
- Mention the written guide (link will be added automatically)
- End with subscribe/watch next CTA
- Use 3-4 emojis for visual appeal
- NO hashtags

VIDEO TOPIC: {topic}
VIDEO SUMMARY: {summary}
KEYWORDS: {', '.join(keywords[:3])}

OUTPUT: Just the post text. No quotes, no explanation."""

        try:
            res = _query_llm(self.config, system_prompt, "Generate the summary post.", task="script")
            return res.strip()
        except Exception as e:
            log.info(f"[Community Post] Summary generation failed: {e}")
            return f"New video just dropped! 🎬\n\nWe covered:\n• {keywords[0] if keywords else 'Key concept'}\n• {keywords[1] if len(keywords) > 1 else 'Practical setup'}\n• {keywords[2] if len(keywords) > 2 else 'Pro tips'}\n\n📖 Full written guide with code and links in the description!\n\nWhat should we cover next? 👇"

    def _get_credentials(self, run_dir):
        """Get YouTube API credentials."""
        pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        workspace_dir = os.path.dirname(pipeline_dir)
        config_dir = os.path.join(workspace_dir, "config")
        token_path = os.path.join(config_dir, "token.json")
        client_secrets_path = os.path.join(config_dir, "client_secrets.json")
        
        creds = None
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, self.scopes)
            except Exception:
                creds = None
        
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
            except Exception:
                creds = None
        
        if not creds or not creds.valid:
            if not os.path.exists(client_secrets_path):
                log.info("[Community Post] No client_secrets.json found. Cannot publish.")
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, self.scopes)
                creds = flow.run_local_server(port=0)
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
            except Exception as e:
                log.info(f"[Community Post] OAuth failed: {e}")
                return None
        
        return creds

    def _publish_post(self, post, run_dir):
        """Publishing not supported via YouTube Data API v3 — communityPosts endpoint is private."""
        log.info("[Community Post] Publishing via API is not supported. Use YouTube Studio to post manually.")
        return None

