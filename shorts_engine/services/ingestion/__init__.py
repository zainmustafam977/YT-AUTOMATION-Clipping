"""
Ingestion services package: YouTube downloader, local video loader, and watch folder daemon.
"""

from shorts_engine.services.ingestion.downloader import IngestionService
from shorts_engine.services.ingestion.local_loader import LocalVideoIngestionService
from shorts_engine.services.ingestion.watch_folder import WatchFolderDaemon, is_file_locked
from shorts_engine.services.ingestion.oauth_flow import YouTubeDeviceOAuth
from shorts_engine.services.ingestion.session_harvester import SessionHarvester
from shorts_engine.services.ingestion.policy_gate import PolicyGate
from shorts_engine.services.ingestion.proxy_manager import ProxyManager
from shorts_engine.services.ingestion.hotfolder_watcher import HotfolderWatcher, hotfolder_watcher

__all__ = [
    "IngestionService",
    "LocalVideoIngestionService",
    "WatchFolderDaemon",
    "is_file_locked",
    "YouTubeDeviceOAuth",
    "SessionHarvester",
    "PolicyGate",
    "ProxyManager",
    "HotfolderWatcher",
    "hotfolder_watcher",
]
