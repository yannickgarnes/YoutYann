"""
SEO Engine — AI-powered metadata optimization for maximum discoverability.

Features:
- Title optimization (power words, character limits)
- Description with CTA and hashtags
- Tag generation based on niche + trends
- Multi-platform adaptation (YouTube, TikTok, IG)
"""

import logging

logger = logging.getLogger(__name__)


class SEOEngine:
    """Optimizes video metadata for algorithm performance."""

    # Power words that increase CTR (from viral content research)
    POWER_WORDS = {
        "en": [
            "INSANE", "SHOCKING", "IMPOSSIBLE", "EPIC", "UNBELIEVABLE",
            "NEVER", "SECRET", "BANNED", "LEAKED", "FIRST EVER",
            "YOU WON'T BELIEVE", "GONE WRONG", "CAUGHT ON CAMERA",
        ],
        "es": [
            "INCREÍBLE", "IMPOSIBLE", "ÉPICO", "NUNCA VISTO",
            "SECRETO", "PROHIBIDO", "FILTRADO", "NO CREERÁS",
            "SALE MAL", "CAPTADO EN CÁMARA",
        ],
    }

    # Hashtag sets by niche
    NICHE_TAGS = {
        "gaming": ["#gaming", "#gamer", "#gameplay", "#epic", "#clutch", "#shorts"],
        "entertainment": ["#entertainment", "#funny", "#viral", "#trending", "#shorts"],
        "satisfying": ["#satisfying", "#oddlysatisfying", "#asmr", "#relaxing", "#shorts"],
        "sports": ["#sports", "#highlights", "#goals", "#epic", "#shorts"],
        "tech": ["#tech", "#technology", "#gadgets", "#review", "#shorts"],
        "comedy": ["#comedy", "#funny", "#lol", "#humor", "#shorts"],
    }

    def __init__(self, gemini_api_key: str = None):
        self.gemini_key = gemini_api_key

    def optimize_title(self, title: str, niche: str = "", lang: str = "en") -> str:
        """Optimize title for maximum CTR."""
        # Ensure title is short (YouTube Shorts best: under 40 chars)
        title = title[:70]

        # Add emoji for higher CTR
        emoji_map = {
            "gaming": "🎮", "entertainment": "🔥",
            "satisfying": "😍", "sports": "⚽",
            "tech": "💻", "comedy": "😂",
        }
        emoji = emoji_map.get(niche, "🔥")

        if not any(e in title for e in "🔥😱🤯😍💀⚡🎮"):
            title = f"{emoji} {title}"

        return title

    def generate_description(self, title: str, niche: str = "",
                              lang: str = "en", tags: list = None) -> str:
        """Generate SEO-optimized description."""
        tags = tags or self.NICHE_TAGS.get(niche, ["#shorts", "#viral"])

        cta = {
            "en": "👆 SUBSCRIBE for more! Like & Share if this blew your mind! 🔥",
            "es": "👆 ¡SUSCRÍBETE para más! Dale like y comparte si te voló la cabeza 🔥",
        }

        tag_str = " ".join(tags[:12])

        desc = f"{title}\n\n{cta.get(lang, cta['en'])}\n\n{tag_str}"
        return desc[:5000]  # YouTube max

    def get_optimal_tags(self, niche: str, custom_tags: list = None) -> list:
        """Get optimized tag list for the niche."""
        base = self.NICHE_TAGS.get(niche, ["#shorts", "#viral"])
        custom = custom_tags or []

        # Merge, deduplicate, clean
        all_tags = []
        seen = set()
        for tag in custom + base:
            clean = tag.strip().lower()
            if clean and clean not in seen:
                seen.add(clean)
                all_tags.append(tag.strip())

        # Always include essential tags
        for essential in ["#shorts", "#viral", "#trending"]:
            if essential not in seen:
                all_tags.append(essential)

        return all_tags[:30]  # YouTube max
