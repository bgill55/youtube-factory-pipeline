import os
import json
import socket
import sys
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from youtube_factory.logging_utils import get_logger

log = get_logger("agent_uploader")


class UploaderAgent:
    def __init__(self, config):
        self.config = config
        self.scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly"
        ]

    def get_credentials(self, run_dir):
        # Resolve config directory relative to the workspace directory (which is parent of pipeline)
        pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        workspace_dir = os.path.dirname(pipeline_dir)
        config_dir = os.path.join(workspace_dir, "config")
        token_path = os.path.join(config_dir, "token.json")
        client_secrets_path = os.path.join(config_dir, "client_secrets.json")

        creds = None
        # Load cached token if exists
        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, self.scopes)
            except Exception:
                creds = None

        # If token is invalid or doesn't exist, refresh or authorize
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    # Save refreshed token
                    with open(token_path, "w", encoding="utf-8") as token_file:
                        token_file.write(creds.to_json())
                    return creds
                except Exception:
                    pass

            # Perform complete authorization flow
            if not os.path.exists(client_secrets_path):
                raise FileNotFoundError(
                    f"\n[YouTube Uploader] Client secrets file not found at: {client_secrets_path}\n"
                    "Skipping upload. To configure YouTube automation:\n"
                    "1. Go to Google Cloud Console (https://console.cloud.google.com/)\n"
                    "2. Create a project and enable 'YouTube Data API v3'.\n"
                    "3. Create OAuth 2.0 Client credentials (Desktop Application).\n"
                    "4. Download OAuth client secrets JSON and save it as 'config/client_secrets.json'.\n"
                )

            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, self.scopes)
            creds = flow.run_local_server(port=0)
            
            # Save token for next time
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())

        return creds

    def run(self, inputs):
        video_file = inputs.get("video_file")
        run_dir = inputs.get("run_dir")
        
        if not video_file or not os.path.exists(video_file):
            raise FileNotFoundError(f"Video file not found for upload: {video_file}")

        # Get video metadata details
        idea_output = inputs.get("idea_output", {})
        visuals_output = inputs.get("visuals_output", {})
        
        selected_topic = idea_output.get("selected_topic", "Automated YouTube Video")
        concept_summary = idea_output.get("concept_summary", "Created automatically by YouTube Factory Pipeline.")
        keywords = idea_output.get("keywords", ["ai", "automation"])

        # Use LLM-generated description and tags if available
        llm_description = idea_output.get("description", "")
        llm_tags = idea_output.get("tags", [])

        # Try to get suggestions from visual metadata
        thumbnail_meta = visuals_output.get("thumbnail_metadata", {})
        title_suggestions = thumbnail_meta.get("title_suggestions", [])
        video_title = title_suggestions[0] if title_suggestions else selected_topic

        # Clean tags - use LLM tags if available, else generate from keywords
        if llm_tags:
            tags = [str(k)[:25] for k in llm_tags][:15]
        else:
            tags = [str(k)[:25] for k in keywords][:10]
            # Add related search terms from SEO research
            seo_research = idea_output.get("seo_research", {})
            for sug in seo_research.get("suggestions", [])[:3]:
                if len(sug) <= 25 and sug not in tags:
                    tags.append(sug)
            tags = tags[:15]  # API tags limit

        upload_settings = self.config.get("upload_settings", {})
        privacy_status = upload_settings.get("privacy_status", "private")

        # 1. Authorize
        try:
            creds = self.get_credentials(run_dir)
        except FileNotFoundError as e:
            # Non-blocking credentials error: log details and return success for local file creation
            log.warning("YouTube credentials not configured: %s", str(e))
            return {
                "status": "SKIPPED_CREDENTIALS_MISSING",
                "video_file": video_file,
                "message": "YouTube upload skipped because credentials are not configured. The final MP4 is ready locally."
            }

        # 2. Upload
        log.info("Uploading %s to YouTube as '%s'...", video_file, video_title)
        youtube = build("youtube", "v3", credentials=creds)

        # 3. Compile a professional, SEO-optimized video description
        description_lines = []
        description_lines.append(concept_summary)
        description_lines.append("")
        
        # Add SEO research keywords if available
        seo_research = idea_output.get("seo_research", {})
        if seo_research:
            related = seo_research.get("related", [])
            how_to = seo_research.get("how_to", [])
            if related or how_to:
                description_lines.append("===========================================")
                description_lines.append("\U0001f50d RELATED TOPICS:")
                for q in (related + how_to)[:5]:
                    description_lines.append(f"\u2022 {q}")
                description_lines.append("===========================================")
                description_lines.append("")
        
        # Add featured links/citations if present
        featured_links = idea_output.get("featured_links", [])
        if featured_links:
            description_lines.append("===========================================")
            description_lines.append("\U0001f517 FEATURED LINKS & RESOURCES:")
            for link in featured_links:
                name = link.get("name", "Link")
                url = link.get("url", "")
                if url:
                    description_lines.append(f"\u2022 {name}: {url}")
            description_lines.append("===========================================")
            description_lines.append("")
            
        # Add channel branding footer
        channel_name = self.config.get("channel", {}).get("name", "Weight and See")
        channel_handle = self.config.get("channel", {}).get("handle", "@WeightnSee")
        channel_tagline = self.config.get("channel", {}).get("tagline", "Your signal through the noise of AI.")
        
        description_lines.append(f"\U0001f514 SUBSCRIBE TO {channel_name.upper()}:")
        description_lines.append(f"{channel_tagline}")
        clean_handle = channel_handle.replace("@", "")
        description_lines.append(f"Subscribe: https://www.youtube.com/@{clean_handle}")
        description_lines.append("")
        
        description_lines.append("\U0001f4f2 FOLLOW US ON SOCIALS:")
        description_lines.append(f"\u2022 X (Twitter): https://x.com/{clean_handle}")
        description_lines.append(f"\u2022 Instagram: https://instagram.com/{clean_handle}")
        description_lines.append("")
        
        # Add guide link if available
        guide_path = os.path.join(run_dir, "guide.html")
        
        # Create trackable short links
        import re
        topic_slug = re.sub(r'[^a-z0-9]+', '-', idea_output.get("selected_topic", "video").lower()).strip('-')[:50]
        from youtube_factory.shortio import ShortioManager
        shortio_key = os.environ.get("SHORTIO_API_KEY", "sk_tHnZp3W2JMePecTg")
        shortio = ShortioManager(shortio_key)
        
        if os.path.exists(guide_path):
            # Check for deployed URL from guide generator output
            guide_output = inputs.get("guide_output", {})
            guide_url = guide_output.get("guide_url") if guide_output else None
            
            if guide_url:
                utm_guide = f"{guide_url}?utm_source=youtube&utm_medium=description&utm_campaign={topic_slug}"
                short_guide = shortio.create_short_link(utm_guide, topic_slug) or utm_guide
                description_lines.append("\U0001f4d6 FULL WRITTEN GUIDE:")
                description_lines.append("Complete step-by-step guide with code examples, tools, and resources:")
                description_lines.append(short_guide)
            else:
                description_lines.append("\U0001f4d6 FULL WRITTEN GUIDE:")
                description_lines.append("Complete step-by-step guide with code examples, tools, and resources:")
                description_lines.append("Guide available soon - check back later!")
            description_lines.append("")
        
        # Add guides wiki link (all guides collection)
        wiki_url = "https://github.com/bgill55/-weightandsee-guides/blob/Master/README.md"
        utm_wiki = f"{wiki_url}?utm_source=youtube&utm_medium=description&utm_campaign={topic_slug}"
        short_wiki = shortio.create_short_link(utm_wiki, f"wiki-{topic_slug}") or utm_wiki
        description_lines.append("\U0001f4da ALL GUIDES & TUTORIALS:")
        description_lines.append("Browse every guide with code, resources, and step-by-step instructions:")
        description_lines.append(short_wiki)
        description_lines.append("")
        
        # Add hashtags - use SEO-optimized if available, else generate from keywords
        optimized_hashtags = idea_output.get("optimized_hashtags", [])
        if optimized_hashtags:
            import re
            hashtag_list = []
            for h in optimized_hashtags:
                if h.startswith("#"):
                    tag = h[1:]
                else:
                    tag = h
                cleaned = re.sub(r'[^a-zA-Z0-9]', '', tag)
                if cleaned:
                    hashtag_list.append(f"#{cleaned}")
            hashtag_list = hashtag_list[:5]
        else:
            import re
            hashtag_list = []
            for t in keywords:
                if t:
                    tag = re.sub(r'[^a-zA-Z0-9]', '', t)
                    if tag:
                        hashtag_list.append(f"#{tag}")
            brand_hashtag = f"#{re.sub(r'[^a-zA-Z0-9]', '', channel_name)}"
            if brand_hashtag not in hashtag_list:
                hashtag_list.insert(0, brand_hashtag)
        description_lines.append(" ".join(hashtag_list[:5]))
        
        # Use LLM-generated description if available, else fall back to generated one
        if llm_description and len(llm_description) > 50:
            # Even with LLM description, append validated featured_links and guide URL
            appendix = ""
            if featured_links:
                appendix += "\n\n===========================================\n\U0001f517 FEATURED LINKS & RESOURCES:\n"
                for link in featured_links:
                    name = link.get("name", "Link")
                    url = link.get("url", "")
                    if url:
                        appendix += f"\u2022 {name}: {url}\n"
                appendix += "==========================================="
            # Add guide URL if available
            guide_url = None
            guide_output = inputs.get("guide_output", {})
            if guide_output:
                guide_url = guide_output.get("guide_url")
            if not guide_url:
                # Check if guide_url was already built into description_lines
                for line in description_lines:
                    if "github.io" in line and "guides" in line:
                        guide_url = line.strip()
                        break
            if guide_url:
                # Use short.io tracked link
                utm_guide = f"{guide_url}?utm_source=youtube&utm_medium=description&utm_campaign={topic_slug}"
                short_guide = shortio.create_short_link(utm_guide, topic_slug) or utm_guide
                appendix += f"\n\n\U0001f4d6 FULL WRITTEN GUIDE:\nComplete step-by-step guide with code examples, tools, and resources:\n{short_guide}"
            # Add wiki link with short.io tracking
            wiki_url = "https://github.com/bgill55/-weightandsee-guides/blob/Master/README.md"
            utm_wiki = f"{wiki_url}?utm_source=youtube&utm_medium=description&utm_campaign={topic_slug}"
            short_wiki = shortio.create_short_link(utm_wiki, f"wiki-{topic_slug}") or utm_wiki
            appendix += f"\n\n\U0001f4da ALL GUIDES & TUTORIALS:\nBrowse every guide with code, resources, and step-by-step instructions:\n{short_wiki}"
            if appendix:
                video_description = llm_description.rstrip() + appendix
            else:
                video_description = llm_description
        else:
            video_description = "\n".join(description_lines)

        body = {
            "snippet": {
                "title": video_title[:95], # Limit length
                "description": video_description,
                "tags": tags,
                "categoryId": "28" # Science & Technology Category
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            video_file, 
            mimetype="video/mp4", 
            chunksize=1024 * 1024 * 2, 
            resumable=True
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        # Upload chunk-by-chunk
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info("Upload progress: %d%%", int(status.progress() * 100))

        video_id = response.get("id")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # 4. Upload custom thumbnail if exists in run directory
        thumbnail_path = os.path.join(run_dir, "thumbnail.jpg")
        if os.path.exists(thumbnail_path):
            try:
                log.info("Uploading custom thumbnail from %s...", thumbnail_path)
                thumbnail_request = youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
                )
                thumbnail_request.execute()
                log.info("Custom thumbnail uploaded successfully!")
            except Exception as e:
                log.warning("Could not upload custom thumbnail: %s", str(e))
                log.info("Note: Custom thumbnails require YouTube channel phone verification (standard API rule).")

        log.info("Video uploaded successfully! Video URL: %s", video_url)

        # Auto-categorize into playlist
        try:
            from youtube_factory.playlists import PlaylistManager
            pm = PlaylistManager(self.config)
            pm.get_service(run_dir)
            topic = idea_output.get("selected_topic", "")
            pm.categorize_video(video_id, topic, video_description)
        except Exception as e:
            log.warning("Playlist categorization skipped: %s", e)

        result = {
            "status": "SUCCESS",
            "video_id": video_id,
            "video_url": video_url,
            "video_file": video_file
        }

        # Upload short if it was generated
        short_output = inputs.get("short_output")
        if short_output and short_output.get("status") == "SUCCESS" and short_output.get("video_file"):
            short_file = short_output["video_file"]
            if os.path.exists(short_file):
                log.info("Uploading short: %s", short_file)
                try:
                    short_result = self.upload_short({
                        "short_file": short_file,
                        "run_dir": run_dir,
                        "idea_output": idea_output,
                        "visuals_output": visuals_output,
                        "steps": {"UPLOAD": {"output": result}},
                        "guide_output": inputs.get("guide_output")
                    })
                    result["short_url"] = short_result.get("video_url")
                    result["short_id"] = short_result.get("video_id")
                    log.info("Short uploaded: %s", short_result.get("video_url"))
                except Exception as e:
                    log.warning("Short upload failed: %s", e)
            else:
                log.warning("Short file not found: %s", short_file)

        return result

    def upload_short(self, inputs):
        """Upload a vertical Short MP4 to YouTube.

        Shorts do NOT require channel phone-verification — any account can upload
        them via the Data API v3 as long as the video is \u2264 60 s and 9:16.
        We inject #Shorts into title and description so YouTube's classifier
        picks it up immediately.
        """
        short_file = inputs.get("short_file")
        run_dir    = inputs.get("run_dir")

        if not short_file or not os.path.exists(short_file):
            raise FileNotFoundError(f"Short video file not found: {short_file}")

        idea_output    = inputs.get("idea_output", {})
        visuals_output = inputs.get("visuals_output", {})

        selected_topic = idea_output.get("selected_topic", "Automated YouTube Short")
        concept_summary = idea_output.get("concept_summary", "")
        keywords = idea_output.get("keywords", ["ai", "automation"])

        # Build a punchy Shorts title: prepend hook and append #Shorts
        thumbnail_meta   = visuals_output.get("thumbnail_metadata", {})
        title_suggestions = thumbnail_meta.get("title_suggestions", [])
        base_title = title_suggestions[0] if title_suggestions else selected_topic
        # Truncate so the #Shorts tag always fits within the 100-char YouTube limit
        max_base = 87  # 87 + " #Shorts" = 95 chars (safe)
        short_title = f"{base_title[:max_base]} #Shorts"

        tags = [str(k)[:25] for k in keywords][:14] + ["shorts"]

        upload_settings = self.config.get("upload_settings", {})
        privacy_status  = upload_settings.get("privacy_status", "private")

        # 1. Authorize
        try:
            creds = self.get_credentials(run_dir)
        except FileNotFoundError as e:
            log.warning("YouTube Short upload skipped \u2014 credentials not configured: %s", str(e))
            return {
                "status": "SKIPPED_CREDENTIALS_MISSING",
                "short_file": short_file,
                "message": "YouTube Short upload skipped \u2014 credentials not configured. The Short MP4 is ready locally."
            }

        # 2. Build Shorts description (concise, mobile-first)
        channel_name   = self.config.get("channel", {}).get("name", "Weight and See")
        channel_handle = self.config.get("channel", {}).get("handle", "@WeightnSee")
        clean_handle   = channel_handle.replace("@", "")

        # Get guide output and shorten the link if available
        guide_output = inputs.get("guide_output", {})
        guide_url = guide_output.get("guide_url") if guide_output else None
        short_guide = None
        if guide_url:
            try:
                from youtube_factory.shortio import ShortioManager
                import re
                shortio_key = os.environ.get("SHORTIO_API_KEY", "sk_tHnZp3W2JMePecTg")
                shortio = ShortioManager(shortio_key)
                topic_slug = re.sub(r'[^a-zA-Z0-9]', '-', selected_topic).strip('-').lower()
                topic_slug = topic_slug[:25]
                utm_guide = f"{guide_url}?utm_source=youtube&utm_medium=shorts&utm_campaign={topic_slug}"
                short_guide = shortio.create_short_link(utm_guide, f"sh-{topic_slug}") or utm_guide
            except Exception as e:
                log.warning("Shortio link creation failed for Short: %s", e)
                short_guide = guide_url

        description_lines = []
        if concept_summary:
            # Use first 2 sentences of summary for mobile-length description
            sentences = concept_summary.split(". ")
            description_lines.append(". ".join(sentences[:2]).strip() + ("." if not sentences[0].endswith(".") else ""))
            description_lines.append("")

        # Link back to guide and long-form video if available
        has_links = False
        if short_guide:
            description_lines.append(f"\U0001f4d6 Full Guide: {short_guide}")
            has_links = True
            
        long_form_steps = inputs.get("steps", {})
        long_video_id = (long_form_steps.get("UPLOAD", {}) or {}).get("output", {}).get("video_id")
        if long_video_id:
            description_lines.append(f"\u25b6\ufe0f Full video: https://www.youtube.com/watch?v={long_video_id}")
            has_links = True
            
        if has_links:
            description_lines.append("")

        description_lines.append(f"\U0001f514 Subscribe: https://www.youtube.com/@{clean_handle}")
        description_lines.append("")
        hashtag_list = [f"#{t.strip().replace(' ', '')}" for t in keywords if t]
        brand_hashtag = f"#{channel_name.replace(' ', '')}"
        if brand_hashtag not in hashtag_list:
            hashtag_list.insert(0, brand_hashtag)
        hashtag_list.append("#Shorts")
        description_lines.append(" ".join(hashtag_list[:6]))

        short_description = "\n".join(description_lines)

        # 3. Upload
        log.info("Uploading Short '%s' to YouTube...", short_title)
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": short_title,
                "description": short_description,
                "tags": tags,
                "categoryId": "28"   # Science & Technology
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            short_file,
            mimetype="video/mp4",
            chunksize=1024 * 1024 * 2,
            resumable=True
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        # Upload chunk-by-chunk
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info("Short upload progress: %d%%", int(status.progress() * 100))

        video_id = response.get("id")
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        log.info("Short uploaded successfully! URL: %s", video_url)

        return {
            "status": "SUCCESS",
            "video_id": video_id,
            "video_url": video_url,
            "video_file": short_file
        }

