import requests


class ShortioManager:
    """Creates and manages short links via Short.io for click tracking."""
    
    def __init__(self, api_key, domain="weightnsee.s.gy"):
        self.api_key = api_key
        self.domain = domain
        self.base_url = "https://api.short.io"
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }
    
    def create_short_link(self, long_url, title="", domain=None):
        """Create a short link for a URL."""
        try:
            if domain is None:
                domain = self.domain
            payload = {
                "originalURL": long_url,
                "domain": domain,
            }
            if title:
                # Create a clean slug from the title
                import re
                slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40]
                payload["link"] = slug
            
            response = requests.post(
                f"{self.base_url}/links",
                json=payload,
                headers=self.headers,
                timeout=10,
            )
            
            if response.status_code in (200, 201):
                data = response.json()
                short_url = data.get("shortURL", "")
                print(f"[Short.io] Created: {short_url} -> {long_url[:60]}")
                return short_url
            else:
                print(f"[Short.io] Failed ({response.status_code}): {response.text[:200]}")
                return None
        except Exception as e:
            print(f"[Short.io] Error: {e}")
            return None
    
    def get_click_stats(self, short_link_id):
        """Get click statistics for a short link."""
        try:
            response = requests.get(
                f"{self.base_url}/links/{short_link_id}/clicks",
                headers=self.headers,
                timeout=10,
            )
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None


if __name__ == "__main__":
    import os
    
    API_KEY = "sk_tHnZp3W2JMePecTg"
    manager = ShortioManager(API_KEY)
    
    # Test: create a short link
    test_url = "https://bgill55.github.io/-weightandsee-guides/"
    result = manager.create_short_link(test_url, "test-link")
    print(f"Result: {result}")
