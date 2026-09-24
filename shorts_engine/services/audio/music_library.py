"""
Royalty-free background music library catalog and mood-matching manager.
Organizes audio tracks across stylistic genres (cinematic, lofi, upbeat, suspense, epic)
for automated pairing during short-form video rendering.
"""

import logging
import random
from pathlib import Path
from typing import Dict, Any, List, Optional

from shorts_engine.config.settings import settings

logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


class MusicLibraryManager:
    """
    Catalog manager for background music assets.
    Enables automatic selection by genre/mood or randomized rotation.
    """

    def __init__(self, library_dir: Optional[Path] = None):
        self.library_dir = Path(library_dir or getattr(settings, "MUSIC_LIBRARY_DIR", Path("assets/music")))

    def ensure_library_dir(self) -> Path:
        """Ensures the root music assets directory exists."""
        self.library_dir.mkdir(parents=True, exist_ok=True)
        return self.library_dir

    def scan_tracks(self) -> Dict[str, List[Path]]:
        """
        Scans library directory and maps tracks into genres / moods.
        Subdirectories represent categories (e.g. assets/music/cinematic/track1.mp3).
        Tracks in the root directory are mapped to 'general'.
        """
        if not self.library_dir.exists():
            return {}

        catalog: Dict[str, List[Path]] = {}

        for item in self.library_dir.rglob("*"):
            if item.is_file() and item.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                # Determine genre/mood from parent subfolder or filename
                rel_parts = item.relative_to(self.library_dir).parts
                if len(rel_parts) > 1:
                    genre = rel_parts[0].lower()
                else:
                    genre = "general"

                if genre not in catalog:
                    catalog[genre] = []
                catalog[genre].append(item)

        return catalog

    def get_track_for_mood(self, mood_or_genre: Optional[str] = None) -> Optional[Path]:
        """
        Retrieves a track matching requested genre/mood.
        Falls back to 'general' or any available track if requested genre is empty.
        Returns None if library contains no audio tracks.
        """
        catalog = self.scan_tracks()
        if not catalog:
            return None

        key = (mood_or_genre or "").strip().lower()
        if key and key in catalog and catalog[key]:
            return random.choice(catalog[key])

        # Partial matching (e.g. 'cinematic' matching 'cinematic_epic')
        if key:
            for g, tracks in catalog.items():
                if key in g and tracks:
                    return random.choice(tracks)

        # Fallback to general or all tracks
        if "general" in catalog and catalog["general"]:
            return random.choice(catalog["general"])

        all_tracks = [t for tracks in catalog.values() for t in tracks]
        return random.choice(all_tracks) if all_tracks else None

    def list_all_tracks(self) -> List[Dict[str, Any]]:
        """Lists metadata for all discovered music tracks."""
        catalog = self.scan_tracks()
        result = []
        for genre, paths in catalog.items():
            for p in paths:
                result.append({
                    "name": p.stem,
                    "genre": genre,
                    "path": str(p),
                    "file_size": p.stat().st_size,
                    "extension": p.suffix.lower(),
                })
        return result
