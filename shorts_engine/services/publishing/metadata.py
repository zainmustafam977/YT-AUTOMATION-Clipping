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
