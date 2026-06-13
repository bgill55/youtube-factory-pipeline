"""
Comment Auto-Response Agent.
Fetches recent YouTube comments, generates AI responses, and posts replies.
Uses YouTube Data API v3 with youtube.force-ssl scope.
"""
import os
import json
import logging
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


class CommentAgent:
    """Auto-responds to YouTube comments using LLM-generated replies."""

    def __init__(self, config=None):
        self.config = config or {}
        self.scopes = [
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]
        self.channel_handle = self.config.get("channel", {}).get("handle", "@WeightnSee")
        self._channel_id = None

    def _get_credentials(self):
        """Load and refresh OAuth credentials."""
        config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        token_path = os.path.join(config_dir, "token.json")

        if not os.path.exists(token_path):
            raise FileNotFoundError(f"Token file not found at {token_path}")

        creds = Credentials.from_authorized_user_file(token_path, self.scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        return creds

    def _get_youtube_service(self):
        """Build YouTube Data API v3 service."""
        creds = self._get_credentials()
        return build("youtube", "v3", credentials=creds)

    def _get_channel_id(self, youtube=None):
        """Resolve @handle to numeric channel ID (cached)."""
        if self._channel_id:
            return self._channel_id

        if youtube is None:
            youtube = self._get_youtube_service()

        handle = self.channel_handle.lstrip("@")
        request = youtube.channels().list(
            part="id",
            forHandle=handle
        )
        response = request.execute()
        items = response.get("items", [])
        if not items:
            raise ValueError(f"Could not find channel for handle: {self.channel_handle}")

        self._channel_id = items[0]["id"]
        return self._channel_id

    def fetch_recent_comments(self, max_results=25, days=7):
        """Fetch recent comments on the channel's videos."""
        youtube = self._get_youtube_service()
        channel_id = self._get_channel_id(youtube)

        # Get recent videos
        search_req = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            maxResults=10,
            order="date",
            type="video"
        )
        search_resp = search_req.execute()
        video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]

        if not video_ids:
            return []

        # Fetch comments for each video
        all_comments = []
        cutoff = datetime.now() - timedelta(days=days)

        for vid in video_ids:
            try:
                comment_req = youtube.commentThreads().list(
                    part="snippet",
                    videoId=vid,
                    maxResults=20,
                    order="time",
                    textFormat="plainText"
                )
                comment_resp = comment_req.execute()

                for item in comment_resp.get("items", []):
                    snippet = item["snippet"]["topLevelComment"]["snippet"]
                    pub_at = datetime.fromisoformat(
                        snippet["publishedAt"].replace("Z", "+00:00")
                    ).replace(tzinfo=None)

                    if pub_at < cutoff:
                        continue

                    all_comments.append({
                        "comment_id": item["id"],
                        "video_id": vid,
                        "author": snippet.get("authorDisplayName", ""),
                        "author_channel_id": snippet.get("authorChannelId", {}).get("value", ""),
                        "text": snippet.get("textDisplay", ""),
                        "published_at": snippet["publishedAt"],
                        "likes": snippet.get("likeCount", 0),
                        "has_reply": item["snippet"]["totalReplyCount"] > 0,
                        "video_title": "",  # filled below
                    })
            except Exception as e:
                logger.warning(f"Failed to fetch comments for video {vid}: {e}")
                continue

        # Get video titles for display
        if video_ids:
            try:
                vid_req = youtube.videos().list(
                    part="snippet",
                    id=",".join(video_ids[:10])
                )
                vid_resp = vid_req.execute()
                title_map = {v["id"]: v["snippet"]["title"] for v in vid_resp.get("items", [])}
                for c in all_comments:
                    c["video_title"] = title_map.get(c["video_id"], "")
            except Exception:
                pass

        # Filter out comments from the channel owner
        my_channel_id = channel_id
        all_comments = [c for c in all_comments if c["author_channel_id"] != my_channel_id]

        return all_comments

    def generate_reply(self, comment_text, video_title="", channel_name="Weight and See"):
        """Generate a reply to a comment using LLM."""
        from youtube_factory.freellmapi import FreeLLMAPIClient

        freellmapi_cfg = self.config.get("freellmapi", {})
        llm_client = FreeLLMAPIClient(
            base_url=freellmapi_cfg.get("base_url"),
            api_key=freellmapi_cfg.get("api_key"),
            timeout=freellmapi_cfg.get("timeout", 120),
        )

        system_prompt = f"""You are a friendly, helpful YouTube community manager for the "{channel_name}" channel.
You respond to comments on AI, tech, and science videos.

Rules:
- Be warm, genuine, and conversational (not corporate)
- Keep replies concise (1-3 sentences max)
- ALWAYS end your reply with a short engagement question (e.g., "Have you tried X?", "What do you think about Y?", "Would you want to see a video on that?"). Vary the phrasing — don't make it feel formulaic.
- If someone shares an experience, acknowledge it
- If someone asks a question, answer helpfully
- If someone is negative, stay positive and constructive — skip the closing question for hostile comments
- Use emoji sparingly (0-2 per reply max)
- If someone asks if you are AI, be honest — you're an AI assistant helping manage the channel
- Don't make promises or guarantees
- Reference the video topic when relevant"""

        user_msg = f"Video: {video_title}\nComment: {comment_text}\n\nWrite a reply to this comment:"

        try:
            response = llm_client.query_sync(
                user_message=user_msg,
                system_message=system_prompt,
                temperature=0.7,
                max_tokens=150,
            )
            # Clean up response — remove quotes if wrapped
            reply = response.strip().strip('"').strip("'")
            return reply
        except Exception as e:
            logger.error(f"LLM reply generation failed: {e}")
            return None

    def post_reply(self, comment_id, reply_text):
        """Post a reply to a YouTube comment."""
        youtube = self._get_youtube_service()

        body = {
            "snippet": {
                "parentId": comment_id,
                "textOriginal": reply_text,
            }
        }

        request = youtube.comments().insert(
            part="snippet",
            body=body
        )
        response = request.execute()

        return {
            "reply_id": response.get("id", ""),
            "text": reply_text,
            "status": "posted",
            "posted_at": datetime.now().isoformat(),
        }

    def auto_respond(self, max_replies=5, dry_run=True):
        """Fetch comments, generate replies, optionally post them.

        Args:
            max_replies: Max number of replies to generate/post
            dry_run: If True, only generate replies without posting

        Returns:
            dict with comments processed and replies generated
        """
        comments = self.fetch_recent_comments(days=7)

        # Only process comments that don't have replies yet
        unreplied = [c for c in comments if not c["has_reply"]]

        results = {
            "total_comments": len(comments),
            "unreplied_comments": len(unreplied),
            "replies": [],
            "dry_run": dry_run,
        }

        for comment in unreplied[:max_replies]:
            reply_text = self.generate_reply(
                comment["text"],
                video_title=comment.get("video_title", ""),
            )

            reply_data = {
                "comment_id": comment["comment_id"],
                "comment_author": comment["author"],
                "comment_text": comment["text"][:100],
                "video_title": comment.get("video_title", ""),
                "reply_text": reply_text,
                "status": "generated",
            }

            if reply_text and not dry_run:
                try:
                    posted = self.post_reply(comment["comment_id"], reply_text)
                    reply_data["status"] = "posted"
                    reply_data["reply_id"] = posted.get("reply_id", "")
                except Exception as e:
                    logger.error(f"Failed to post reply: {e}")
                    reply_data["status"] = "failed"
                    reply_data["error"] = str(e)

            results["replies"].append(reply_data)

        return results

    def save_results(self, results, run_dir=None):
        """Save comment response results to JSON."""
        if run_dir is None:
            run_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "workspace", "runs",
                datetime.now().strftime("run_%Y%m%d_%H%M%S")
            )
            os.makedirs(run_dir, exist_ok=True)

        output_path = os.path.join(run_dir, "comment_responses.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        logger.info(f"Comment response results saved to {output_path}")
        return output_path


if __name__ == "__main__":
    agent = CommentAgent()
    comments = agent.fetch_recent_comments(days=30)
    print(f"Found {len(comments)} recent comments")
    for c in comments[:5]:
        print(f"  [{c['author']}] {c['text'][:60]}...")
