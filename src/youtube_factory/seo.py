import requests
import json
import time
from urllib.parse import quote_plus


def get_youtube_suggestions(query, max_results=10):
    """Fetch YouTube autocomplete suggestions for a query.
    Returns list of suggested search terms."""
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&q={quote_plus(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            # Response format: window.google.ac.h(["query",[["suggestion1",0,[512]],...]])
            text = res.text
            # Find the first [ to start parsing
            first_bracket = text.find("[")
            if first_bracket != -1:
                # Find matching closing bracket
                depth = 0
                end_pos = -1
                for i in range(first_bracket, len(text)):
                    if text[i] == "[":
                        depth += 1
                    elif text[i] == "]":
                        depth -= 1
                        if depth == 0:
                            end_pos = i
                            break
                
                if end_pos != -1:
                    json_str = text[first_bracket:end_pos+1]
                    data = json.loads(json_str)
                    # data[0] is query, data[1] is list of suggestions
                    # Each suggestion is [text, score, [metadata]]
                    suggestions = []
                    if len(data) > 1 and isinstance(data[1], list):
                        for item in data[1]:
                            if isinstance(item, list) and len(item) > 0:
                                # First element is the suggestion text
                                suggestions.append(item[0])
                            elif isinstance(item, str):
                                suggestions.append(item)
                    return suggestions[:max_results]
    except Exception as e:
        print(f"[SEO] YouTube suggestions failed for '{query}': {e}")
    return []


def score_title(title, keywords):
    """Score a title based on search volume indicators.
    Higher score = better SEO potential."""
    score = 50  # Base score
    title_lower = title.lower()
    words = title_lower.split()
    
    # Length scoring (50-70 chars is optimal for YouTube)
    char_len = len(title)
    if 50 <= char_len <= 70:
        score += 15
    elif 40 <= char_len <= 80:
        score += 10
    elif char_len < 30 or char_len > 90:
        score -= 10
    
    # Keyword presence scoring
    for kw in keywords:
        if kw.lower() in title_lower:
            score += 8
    
    # Power words that drive CTR
    power_words = ["how", "why", "best", "top", "secret", "hidden", "truth", 
                   "must", "watch", "insane", "crazy", "shocking", "urgent",
                   "breaking", "finally", "real", "honest", "review"]
    for pw in power_words:
        if pw in title_lower:
            score += 3
    
    # Avoid clickbait penalties
    clickbait_penalty = ["click here", "you won't believe", "shocked"]
    for cb in clickbait_penalty:
        if cb in title_lower:
            score -= 20
    
    # Question format bonus (drives curiosity)
    if "?" in title:
        score += 5
    
    # Number bonus (listicles perform well)
    if any(c.isdigit() for c in title):
        score += 3
    
    return min(max(score, 0), 100)


def optimize_titles(candidate_titles, keywords, max_results=3):
    """Research and score candidate titles, return top performers."""
    scored = []
    
    for title in candidate_titles:
        # Get search suggestions related to this title
        words = [w for w in title.split() if len(w) > 3][:3]
        query = " ".join(words)
        suggestions = get_youtube_suggestions(query)
        time.sleep(0.5)  # Rate limit
        
        # Score based on SEO factors
        base_score = score_title(title, keywords)
        
        # Bonus if title matches popular suggestions
        suggestion_bonus = 0
        for sug in suggestions:
            sug_lower = sug.lower()
            title_lower = title.lower()
            # Check if any significant words overlap
            title_words = set(title_lower.split())
            sug_words = set(sug_lower.split())
            overlap = len(title_words & sug_words)
            if overlap >= 2:
                suggestion_bonus += 10
        
        final_score = base_score + suggestion_bonus
        scored.append({
            "title": title,
            "score": final_score,
            "matching_suggestions": suggestions[:5]
        })
    
    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_results]


def generate_hashtags(topic, keywords, max_hashtags=5):
    """Generate optimized hashtags based on topic and keywords."""
    import re
    hashtags = []
    
    # Core topic hashtag - shorten it if it has colons, question marks, dashes or is very long
    short_topic = topic
    if ":" in short_topic:
        short_topic = short_topic.split(":")[0].strip()
    if "-" in short_topic:
        short_topic = short_topic.split("-")[0].strip()
    if "?" in short_topic:
        short_topic = short_topic.split("?")[0].strip()
        
    words = short_topic.split()
    if len(words) > 3:
        short_topic = " ".join(words[:3])
        
    topic_tag = re.sub(r'[^a-zA-Z0-9]', '', short_topic)
    if topic_tag:
        hashtags.append(f"#{topic_tag}")
    
    # Keyword hashtags
    for kw in keywords[:4]:
        tag = re.sub(r'[^a-zA-Z0-9]', '', kw)
        if tag and tag.lower() not in [h[1:].lower() for h in hashtags]:
            hashtags.append(f"#{tag}")
    
    # Always include niche hashtags
    niche_tags = ["#AI", "#Tech", "#Automation", "#Technology", "#Innovation"]
    for nt in niche_tags:
        if len(hashtags) >= max_hashtags:
            break
        if nt.lower() not in [h.lower() for h in hashtags]:
            hashtags.append(nt)
    
    return hashtags[:max_hashtags]


def get_trending_tags(niche="AI"):
    """Fetch trending hashtags from YouTube search for a niche."""
    suggestions = get_youtube_suggestions(f"{niche} tutorial 2026")
    tags = []
    for sug in suggestions:
        # Extract hashtag-worthy phrases
        if len(sug.split()) <= 3:
            tag = sug.replace(" ", "")
            if not tag.startswith("#"):
                tag = f"#{tag}"
            tags.append(tag)
    return tags[:5]


def research_competition(topic):
    """Get search volume indicators for a topic."""
    suggestions = get_youtube_suggestions(topic)
    related_queries = get_youtube_suggestions(f"{topic} tutorial")
    how_queries = get_youtube_suggestions(f"how to {topic}")
    
    return {
        "suggestions": suggestions,
        "related": related_queries,
        "how_to": how_queries,
        "volume_indicator": len(suggestions)  # More suggestions = more search volume
    }
