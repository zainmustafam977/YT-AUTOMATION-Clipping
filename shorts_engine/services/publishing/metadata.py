"""
YouTube Shorts Metadata Generator and Sanitizer.
Ensures strict character limits (Title <= 100, Description <= 5000) and hashtag formatting.
"""

from typing import List, Dict, Any, Optional


class ShortsMetadata:
    def __init__(
        self,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        privacy_status: str = "unlisted",
        category_id: str = "22",
        made_for_kids: bool = False,
        publish_at: Optional[str] = None,
        default_language: Optional[str] = None,
        license: str = "youtube",
        embeddable: bool = True,
    ):
        self.title = self._clean_title(title)
        self.description = self._clean_description(description, tags)
        self.tags = tags or ["#shorts", "#viral", "#highlights"]
        self.privacy_status = privacy_status
        self.category_id = str(category_id or "22")
        self.made_for_kids = bool(made_for_kids)
        self.publish_at = publish_at
        self.default_language = default_language
        self.license = license
        self.embeddable = embeddable

    def _clean_title(self, raw_title: str) -> str:
        title = raw_title.strip()
        if "#Shorts" not in title and "#shorts" not in title:
            title = f"{title} #Shorts"
        # Enforce strict YouTube 100 character limit
        if len(title) > 100:
            title = title[:92].strip() + "... #Shorts"
        return title[:100]

    def _clean_description(self, raw_desc: str, tags: Optional[List[str]]) -> str:
        desc = raw_desc.strip()
        tag_str = " ".join(tags) if tags else "#Shorts"
        if tag_str not in desc:
            desc = f"{desc}\n\n{tag_str}"
        return desc[:5000]

    def to_dict(self) -> Dict[str, Any]:
        snippet: Dict[str, Any] = {
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "categoryId": self.category_id,
        }
        if self.default_language:
            snippet["defaultLanguage"] = self.default_language

        status: Dict[str, Any] = {
            "selfDeclaredMadeForKids": self.made_for_kids,
            "embeddable": self.embeddable,
            "license": self.license,
        }

        # YouTube Data API requires privacyStatus to be 'private' if publishAt is set
        if self.publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = self.publish_at
        else:
            status["privacyStatus"] = self.privacy_status

        return {
            "snippet": snippet,
            "status": status,
        }


def extract_clip_id_from_description(description: str) -> Optional[str]:
    """Parses [c:<clip_id>] or [clip:<clip_id>] token from a YouTube description string."""
    if not description:
        return None
    import re
    match = re.search(r'\[(?:c|clip):([^\]]+)\]', description)
    return match.group(1) if match else None


def check_duplicate_upload(clip_id: str, channel_recent_descriptions: List[str]) -> bool:
    """Returns True if any description in the list contains [c:<clip_id>] or [clip:<clip_id>]."""
    token_c = f"[c:{clip_id}]"
    token_clip = f"[clip:{clip_id}]"
    for desc in channel_recent_descriptions:
        if desc and (token_c in desc or token_clip in desc):
            return True
    return False


def build_clip_metadata(clip: Any) -> ShortsMetadata:
    """Extracts or infers YouTube title, description, and hashtags for a clip, preserving SEO and channel defaults."""
    cand = getattr(clip, "candidate", None)
    title = getattr(clip, "generated_title", None) or (cand.hook_text if cand else "Viral Short")
    if len(title) > 90:
        title = title[:87] + "..."
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"

    token = f"[c:{clip.id}]"
    legacy_token = f"[clip:{clip.id}]"
    gen_desc = getattr(clip, "generated_description", None)
    if gen_desc and len(gen_desc.strip()) > 10:
        description = gen_desc
        if token not in description:
            description = f"{description}\n\n{token} {legacy_token}"
        elif legacy_token not in description:
            description = f"{description} {legacy_token}"
    else:
        base_desc = cand.hook_text if cand else title
        description = f"{base_desc}\n\n#shorts #viral #trending\n\n{token} {legacy_token}"

    tags = None
    gen_tags = getattr(clip, "generated_tags", None)
    if gen_tags:
        try:
            import json
            parsed = json.loads(gen_tags)
            if isinstance(parsed, list) and parsed:
                tags = [str(t) for t in parsed]
        except Exception:
            tags = None

    channel = None
    if cand and cand.source_video and cand.source_video.channel:
        channel = cand.source_video.channel

    channel_defaults = getattr(channel, "upload_defaults", {}) if channel else {}
    if not tags:
        tags = ["#shorts", "#viral", "#trending"]
        if channel:
            tags.append(channel.name.lower().replace(" ", ""))
        if channel_defaults.get("default_tags"):
            raw_def_tags = channel_defaults["default_tags"]
            if isinstance(raw_def_tags, list):
                def_tags = [str(t).strip() for t in raw_def_tags if str(t).strip()]
            else:
                def_tags = [t.strip() for t in str(raw_def_tags).split(",") if t.strip()]
            tags.extend(def_tags)

    privacy = channel_defaults.get("privacy_status", "unlisted")
    category_id = channel_defaults.get("category_id", "22")
    made_for_kids = bool(channel_defaults.get("made_for_kids", False))
    language = channel_defaults.get("default_language", "en")
    license_type = channel_defaults.get("license", "youtube")

    return ShortsMetadata(
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy,
        category_id=category_id,
        made_for_kids=made_for_kids,
        default_language=language,
        license=license_type,
    )

