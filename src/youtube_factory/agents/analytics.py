"""
YouTube Analytics & Performance Dashboard Agent.
Pulls video stats, watch time, retention, demographics, traffic sources.
Uses YouTube Data API v3 + YouTube Analytics API v2.
"""
import os
import re
import json
import logging
import time
import hashlib
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from youtube_factory.logging_utils import get_logger
logger = get_logger("agent_analytics")

_cache = {}
CACHE_TTL = 3600  # 1 hour default


def _cache_key(method, *args, **kwargs):
    raw = f"{method}:{args}:{sorted(kwargs.items())}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key, data):
    _cache[key] = {"data": data, "ts": time.time()}


def clear_cache():
    _cache.clear()


class AnalyticsAgent:
    """Fetches and analyzes YouTube channel performance."""

    def __init__(self, config=None):
        self.config = config or {}
        self.scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
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

    def _get_youtube_analytics_service(self):
        """Build YouTube Analytics API v2 service."""
        creds = self._get_credentials()
        return build("youtubeAnalytics", "v2", credentials=creds)

    def _get_channel_id(self, youtube=None):
        """Resolve @handle to numeric channel ID (cached)."""
        if self._channel_id:
            return self._channel_id

        if youtube is None:
            youtube = self._get_youtube_service()

        handle = self.channel_handle.lstrip("@")
        request = youtube.channels().list(
            part="id,statistics,snippet",
            forHandle=handle
        )
        response = request.execute()
        items = response.get("items", [])
        if not items:
            raise ValueError(f"Could not find channel for handle: {self.channel_handle}")

        self._channel_id = items[0]["id"]
        return self._channel_id

    def _parse_iso_duration(self, duration_iso):
        """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
        match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_iso)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        mins = int(match.group(2) or 0)
        secs = int(match.group(3) or 0)
        return hours * 3600 + mins * 60 + secs

    def _format_duration(self, secs):
        """Format seconds as H:MM:SS or M:SS."""
        hours = secs // 3600
        mins = (secs % 3600) // 60
        s = secs % 60
        if hours > 0:
            return f"{hours}:{mins:02d}:{s:02d}"
        return f"{mins}:{s:02d}"

    # ==================== DATA API v3 METHODS ====================

    def get_channel_stats(self, force_refresh=False):
        """Get overall channel statistics from Data API."""
        if not force_refresh:
            cached = _cache_get("channel_stats")
            if cached:
                return cached
        youtube = self._get_youtube_service()
        channel_id = self._get_channel_id(youtube)

        request = youtube.channels().list(
            part="id,statistics,snippet,contentDetails",
            id=channel_id
        )
        response = request.execute()
        items = response.get("items", [])
        if not items:
            return {}

        ch = items[0]
        stats = ch.get("statistics", {})
        snippet = ch.get("snippet", {})

        result = {
            "channel_id": channel_id,
            "channel_title": snippet.get("title", ""),
            "channel_description": snippet.get("description", ""),
            "subscribers": int(stats.get("subscriberCount", 0)),
            "total_views": int(stats.get("viewCount", 0)),
            "total_videos": int(stats.get("videoCount", 0)),
            "hidden_subs": stats.get("hiddenSubscriberCount", False),
            "uploads_playlist": ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""),
            "fetched_at": datetime.now().isoformat()
        }
        _cache_set("channel_stats", result)
        return result

    def get_video_performance(self, limit=50, force_refresh=False):
        """Fetch all videos with performance stats, sorted by views."""
        cache_key = f"video_performance:{limit}"
        if not force_refresh:
            cached = _cache_get(cache_key)
            if cached:
                return cached
        youtube = self._get_youtube_service()
        channel_id = self._get_channel_id(youtube)

        videos = []
        next_page_token = None

        while True:
            request = youtube.search().list(
                part="snippet",
                channelId=channel_id,
                maxResults=50,
                order="date",
                type="video",
                pageToken=next_page_token
            )
            response = request.execute()

            video_ids = [item["id"]["videoId"] for item in response.get("items", [])]
            if video_ids:
                details_request = youtube.videos().list(
                    part="statistics,contentDetails,snippet",
                    id=",".join(video_ids)
                )
                details = details_request.execute()
                videos.extend(details.get("items", []))

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        results = []
        for v in videos:
            stats = v.get("statistics", {})
            snippet = v.get("snippet", {})
            content = v.get("contentDetails", {})
            duration_iso = content.get("duration", "PT0S")
            duration_secs = self._parse_iso_duration(duration_iso)

            results.append({
                "video_id": v["id"],
                "title": snippet.get("title", ""),
                "description": snippet.get("description", "")[:200],
                "published_at": snippet.get("publishedAt", ""),
                "duration_secs": duration_secs,
                "duration_display": self._format_duration(duration_secs),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "tags": snippet.get("tags", [])[:5],
                "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            })

        results.sort(key=lambda x: x["views"], reverse=True)

        if results:
            max_views = max(r["views"] for r in results) or 1
            for r in results:
                r["engagement_rate"] = round(
                    (r["likes"] + r["comments"]) / max(r["views"], 1) * 100, 2
                )
                r["view_score"] = round(r["views"] / max_views * 100, 1)

        output = results[:limit]
        _cache_set(cache_key, output)
        return output

    # ==================== ANALYTICS API v2 METHODS ====================

    def get_analytics_report(self, metrics, dimensions=None, sort=None, limit=None, start_date=None, end_date=None):
        """Generic YouTube Analytics API query. Returns [] on scope/auth errors."""
        try:
            analytics = self._get_youtube_analytics_service()
            channel_id = self._get_channel_id()

            if end_date is None:
                end_date = datetime.now().strftime("%Y-%m-%d")
            if start_date is None:
                start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

            kwargs = {
                "ids": f"channel=={channel_id}",
                "startDate": start_date,
                "endDate": end_date,
                "metrics": metrics,
            }
            if dimensions:
                kwargs["dimensions"] = dimensions
            if sort:
                kwargs["sort"] = sort
            if limit:
                kwargs["maxResults"] = limit

            request = analytics.reports().query(**kwargs)
            response = request.execute()

            headers = [col["name"] for col in response.get("columnHeaders", [])]
            rows = response.get("rows", [])

            return [dict(zip(headers, row)) for row in rows]
        except Exception as e:
            logger.warning(f"Analytics API report failed (scope missing?): {e}")
            return []

    def get_watch_time_summary(self, days=365):
        """Get total watch time over a period."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        data = self.get_analytics_report(
            metrics="estimatedMinutesWatched,views,averageViewDuration,averageViewPercentage",
            start_date=start_date,
            end_date=end_date,
        )
        return data[0] if data else {}

    def get_watch_time_by_video(self, days=365, limit=20):
        """Get watch time broken down by video."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        return self.get_analytics_report(
            metrics="estimatedMinutesWatched,views,averageViewDuration,averageViewPercentage",
            dimensions="video",
            sort="-estimatedMinutesWatched",
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )

    def get_views_by_day(self, days=30):
        """Get daily views for charts."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        return self.get_analytics_report(
            metrics="views,estimatedMinutesWatched",
            dimensions="day",
            sort="day",
            start_date=start_date,
            end_date=end_date,
        )

    def get_traffic_sources(self, days=365):
        """Get traffic source breakdown."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        return self.get_analytics_report(
            metrics="views,estimatedMinutesWatched",
            dimensions="insightTrafficSourceType",
            sort="-views",
            start_date=start_date,
            end_date=end_date,
        )

    def get_geography(self, days=365, limit=10):
        """Get viewer geography breakdown."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        return self.get_analytics_report(
            metrics="views,estimatedMinutesWatched",
            dimensions="country",
            sort="-views",
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )

    def get_demographics(self, days=365):
        """Get age/gender demographics."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        age_data = self.get_analytics_report(
            metrics="viewerPercentage",
            dimensions="ageGroup",
            sort="ageGroup",
            start_date=start_date,
            end_date=end_date,
        )

        gender_data = self.get_analytics_report(
            metrics="viewerPercentage",
            dimensions="gender",
            start_date=start_date,
            end_date=end_date,
        )

        return {"age_groups": age_data, "genders": gender_data}

    def get_device_types(self, days=365):
        """Get device type breakdown."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        return self.get_analytics_report(
            metrics="views,estimatedMinutesWatched",
            dimensions="deviceType",
            sort="-views",
            start_date=start_date,
            end_date=end_date,
        )

    def get_subscriber_growth(self, days=365):
        """Get subscriber changes over time."""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        return self.get_analytics_report(
            metrics="subscribersGained,subscribersLost",
            dimensions="day",
            sort="day",
            start_date=start_date,
            end_date=end_date,
        )

    # ==================== COMBINED ANALYSIS ====================

    def get_analytics_summary(self, force_refresh=False):
        """Generate a comprehensive analytics summary combining Data API + Analytics API."""
        if not force_refresh:
            cached = _cache_get("analytics_summary")
            if cached:
                return cached
        channel = self.get_channel_stats(force_refresh=force_refresh)
        videos = self.get_video_performance(limit=100, force_refresh=force_refresh)

        # Analytics API data (may fail if scope not yet authorized)
        analytics_data = {}
        try:
            analytics_data["watch_time"] = self.get_watch_time_summary()
        except Exception as e:
            logger.warning(f"Analytics API watch time unavailable: {e}")
            analytics_data["watch_time"] = {"error": str(e)}

        try:
            analytics_data["traffic_sources"] = self.get_traffic_sources()
        except Exception as e:
            logger.warning(f"Analytics API traffic sources unavailable: {e}")
            analytics_data["traffic_sources"] = []

        try:
            analytics_data["demographics"] = self.get_demographics()
        except Exception as e:
            logger.warning(f"Analytics API demographics unavailable: {e}")
            analytics_data["demographics"] = {}

        try:
            analytics_data["devices"] = self.get_device_types()
        except Exception as e:
            logger.warning(f"Analytics API devices unavailable: {e}")
            analytics_data["devices"] = []

        try:
            analytics_data["subscriber_growth"] = self.get_subscriber_growth()
        except Exception as e:
            logger.warning(f"Analytics API subscriber growth unavailable: {e}")
            analytics_data["subscriber_growth"] = []

        # Basic summary
        total_views = sum(v["views"] for v in videos)
        total_likes = sum(v["likes"] for v in videos)
        total_comments = sum(v["comments"] for v in videos)
        avg_views = total_views / len(videos) if videos else 0
        avg_engagement = (
            sum(v["engagement_rate"] for v in videos) / len(videos) if videos else 0
        )

        patterns = self._analyze_patterns(videos)

        result = {
            "channel": channel,
            "summary": {
                "total_videos": len(videos),
                "total_views": total_views,
                "total_likes": total_likes,
                "total_comments": total_comments,
                "avg_views_per_video": int(avg_views),
                "avg_engagement_rate": round(avg_engagement, 2),
                "estimated_minutes_watched": analytics_data.get("watch_time", {}).get("estimatedMinutesWatched", 0),
                "avg_view_duration": analytics_data.get("watch_time", {}).get("averageViewDuration", 0),
                "avg_view_percentage": analytics_data.get("watch_time", {}).get("averageViewPercentage", 0),
            },
            "analytics_api": analytics_data,
            "top_performers": videos[:5],
            "recent_performers": sorted(videos, key=lambda x: x["published_at"], reverse=True)[:5],
            "patterns": patterns,
            "all_videos": videos,
            "fetched_at": datetime.now().isoformat()
        }
        _cache_set("analytics_summary", result)
        return result

    def _analyze_patterns(self, videos):
        """Identify performance patterns across videos."""
        if not videos:
            return {}

        top_quarter = videos[:max(1, len(videos) // 4)]

        top_words = {}
        for v in top_quarter:
            for word in v["title"].lower().split():
                if len(word) > 3:
                    top_words[word] = top_words.get(word, 0) + 1

        top_5 = videos[:5]
        bottom_5 = videos[-5:]
        top_durations = [v["duration_secs"] for v in top_5]
        bottom_durations = [v["duration_secs"] for v in bottom_5]

        day_views = {}
        for v in videos:
            try:
                pub_date = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                day = pub_date.strftime("%A")
                day_views[day] = day_views.get(day, 0) + v["views"]
            except Exception:
                pass

        tag_views = {}
        for v in videos:
            for tag in v.get("tags", []):
                tag_views[tag] = tag_views.get(tag, 0) + v["views"]

        return {
            "top_title_words": dict(sorted(top_words.items(), key=lambda x: -x[1])[:10]),
            "avg_duration_top5": int(sum(top_durations) / len(top_durations)) if top_durations else 0,
            "avg_duration_bottom5": int(sum(bottom_durations) / len(bottom_durations)) if bottom_durations else 0,
            "best_publish_days": dict(sorted(day_views.items(), key=lambda x: -x[1])),
            "top_tags_by_views": dict(sorted(tag_views.items(), key=lambda x: -x[1])[:10]),
        }

    def save_analytics_snapshot(self, run_dir):
        """Save a snapshot of current analytics for tracking over time."""
        try:
            summary = self.get_analytics_summary()
            snapshot_path = os.path.join(run_dir, "analytics_snapshot.json")
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info(f"Analytics snapshot saved to {snapshot_path}")
            return summary
        except Exception as e:
            logger.error(f"Failed to save analytics snapshot: {e}")
            return None

    def get_thumbnail_analysis(self):
        """Compare thumbnails across videos for A/B insight."""
        videos = self.get_video_performance(limit=50)

        return [{
            "video_id": v["video_id"],
            "title": v["title"],
            "views": v["views"],
            "likes": v["likes"],
            "engagement_rate": v["engagement_rate"],
            "thumbnail_url": v["thumbnail_url"],
            "published_at": v["published_at"],
        } for v in videos]


if __name__ == "__main__":
    agent = AnalyticsAgent()
    stats = agent.get_analytics_summary()
    sm = stats["summary"]
    print(f"Channel: {stats['channel']['channel_title']}")
    print(f"Videos: {sm['total_videos']}")
    print(f"Views: {sm['total_views']}")
    print(f"Minutes Watched: {sm['estimated_minutes_watched']}")
    print(f"Avg View Duration: {sm['avg_view_duration']}s")
    print(f"Avg View %: {sm['avg_view_percentage']}%")
    aa = stats["analytics_api"]
    if aa.get("traffic_sources"):
        print("\nTraffic Sources:")
        for ts in aa["traffic_sources"][:5]:
            print(f"  {ts.get('insightTrafficSourceType', 'N/A')}: {ts.get('views', 0)} views")
    if aa.get("demographics", {}).get("genders"):
        print("\nGender:")
        for g in aa["demographics"]["genders"]:
            print(f"  {g.get('gender', 'N/A')}: {g.get('viewerPercentage', 0)}%")
