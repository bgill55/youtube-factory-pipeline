import os
import json
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request


class PlaylistManager:
    """Manages YouTube playlists — creates category playlists and auto-sorts videos."""
    
    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]
    
    # Playlist categories matching our wiki
    CATEGORIES = {
        "Benchmarks & Comparisons": "Head-to-head model showdowns and real-world performance tests",
        "Model Deep Dives": "In-depth analysis of cutting-edge AI models and architectures",
        "Local AI & Self-Hosting": "Run powerful AI models on your own hardware — no cloud required",
        "AI Security": "Threats, vulnerabilities, and defenses in the AI era",
        "Developer Tools & Agents": "AI-powered coding assistants, agents, and developer workflows",
        "Image & Vision": "Image generation, computer vision, and visual AI",
        "No-Code & Automation": "Build AI workflows without writing code",
    }
    
    def __init__(self, config):
        self.config = config
        self.channel_id = config.get("channel", {}).get("channel_id")
        self._service = None
        self._playlist_cache = {}  # title -> playlist_id
    
    def get_service(self, run_dir=None):
        """Get authenticated YouTube API service."""
        if self._service:
            return self._service
        
        config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
        token_path = os.path.join(config_dir, "token.json")
        client_secrets_path = os.path.join(config_dir, "client_secrets.json")
        
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, self.SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(client_secrets_path):
                    print("[Playlist Manager] No client_secrets.json found")
                    return None
                flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, self.SCOPES)
                creds = flow.run_local_server(port=0)
            
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        
        self._service = build("youtube", "v3", credentials=creds)
        return self._service
    
    def create_category_playlists(self):
        """Create playlists for each category if they don't exist."""
        service = self.get_service()
        if not service:
            return {}
        
        # Get existing playlists
        existing = self._get_existing_playlists()
        created = {}
        
        for title, description in self.CATEGORIES.items():
            if title in existing:
                playlist_id = existing[title]
                print(f"[Playlist Manager] Playlist exists: {title} ({playlist_id})")
            else:
                playlist_id = self._create_playlist(title, description)
                if playlist_id:
                    print(f"[Playlist Manager] Created playlist: {title} ({playlist_id})")
                else:
                    print(f"[Playlist Manager] Failed to create: {title}")
                    continue
            
            self._playlist_cache[title] = playlist_id
            created[title] = playlist_id
        
        return created
    
    def add_video_to_category(self, video_id, category_title):
        """Add a video to a category playlist."""
        service = self.get_service()
        if not service:
            return False
        
        # Get or create playlist
        playlist_id = self._get_playlist_id(category_title)
        if not playlist_id:
            print(f"[Playlist Manager] No playlist found for: {category_title}")
            return False
        
        # Check if video already in playlist
        if self._video_in_playlist(video_id, playlist_id):
            print(f"[Playlist Manager] Video {video_id} already in {category_title}")
            return True
        
        # Add to playlist
        try:
            service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        }
                    }
                }
            ).execute()
            print(f"[Playlist Manager] Added {video_id} to {category_title}")
            return True
        except Exception as e:
            print(f"[Playlist Manager] Failed to add video: {e}")
            return False
    
    def categorize_video(self, video_id, title, description=""):
        """Auto-categorize a video based on title/description keywords."""
        text = f"{title} {description}".lower()
        
        keyword_map = {
            "Benchmarks & Comparisons": ["vs", "benchmark", "showdown", "comparison", "speed test", "faceoff"],
            "Model Deep Dives": ["deep dive", "paper", "breakdown", "analysis", "review", "api"],
            "Local AI & Self-Hosting": ["local", "offline", "self-host", "raspberry pi", "laptop", "run on", "vram"],
            "AI Security": ["security", "breach", "hack", "attack", "vulnerability", "firewall", "poison"],
            "Developer Tools & Agents": ["agent", "coding", "cursor", "ide", "developer", "tutorial", "build"],
            "Image & Vision": ["image", "vision", "diffusion", "stable diffusion", "cad", "generate"],
            "No-Code & Automation": ["no-code", "nocode", "automation", "workflow", "flowise", "n8n"],
        }
        
        for category, keywords in keyword_map.items():
            if any(kw in text for kw in keywords):
                return self.add_video_to_category(video_id, category)
        
        # Default: no category matched
        print(f"[Playlist Manager] No category match for: {title}")
        return False
    
    def _get_existing_playlists(self):
        """Fetch all existing playlists for the channel."""
        service = self.get_service()
        if not service:
            return {}
        
        playlists = {}
        try:
            request = service.playlists().list(
                part="snippet",
                channelId=self.channel_id,
                maxResults=50
            )
            response = request.execute()
            
            for item in response.get("items", []):
                title = item["snippet"]["title"]
                playlist_id = item["id"]
                playlists[title] = playlist_id
        except Exception as e:
            print(f"[Playlist Manager] Failed to fetch playlists: {e}")
        
        return playlists
    
    def _create_playlist(self, title, description):
        """Create a new playlist."""
        service = self.get_service()
        if not service:
            return None
        
        try:
            response = service.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": title,
                        "description": description,
                    },
                    "status": {
                        "privacyStatus": "public"
                    }
                }
            ).execute()
            return response["id"]
        except Exception as e:
            print(f"[Playlist Manager] Failed to create playlist '{title}': {e}")
            return None
    
    def _get_playlist_id(self, title):
        """Get playlist ID by title (cached or fetched)."""
        if title in self._playlist_cache:
            return self._playlist_cache[title]
        
        existing = self._get_existing_playlists()
        if title in existing:
            self._playlist_cache[title] = existing[title]
            return existing[title]
        
        # Create it
        description = self.CATEGORIES.get(title, "")
        playlist_id = self._create_playlist(title, description)
        if playlist_id:
            self._playlist_cache[title] = playlist_id
        return playlist_id
    
    def _video_in_playlist(self, video_id, playlist_id):
        """Check if a video is already in a playlist."""
        service = self.get_service()
        if not service:
            return False
        
        try:
            request = service.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50
            )
            response = request.execute()
            
            for item in response.get("items", []):
                if item["snippet"]["resourceId"]["videoId"] == video_id:
                    return True
        except Exception:
            pass
        
        return False
    
    def list_playlists(self):
        """Print all playlists with video counts."""
        service = self.get_service()
        if not service:
            return
        
        try:
            request = service.playlists().list(
                part="snippet,contentDetails",
                channelId=self.channel_id,
                maxResults=50
            )
            response = request.execute()
            
            print("\n=== Channel Playlists ===")
            for item in response.get("items", []):
                title = item["snippet"]["title"]
                count = item["contentDetails"]["itemCount"]
                print(f"  {title}: {count} videos")
            print("========================\n")
        except Exception as e:
            print(f"[Playlist Manager] Failed to list playlists: {e}")


if __name__ == "__main__":
    # Load config
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "config.json")
    with open(config_path) as f:
        config = json.load(f)
    
    manager = PlaylistManager(config)
    
    # Create all category playlists
    print("Creating category playlists...")
    playlists = manager.create_category_playlists()
    print(f"\nCreated/found {len(playlists)} playlists")
    
    # List all
    manager.list_playlists()
