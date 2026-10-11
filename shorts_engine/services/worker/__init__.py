"""
Decoupled worker module for background rendering, quality checks, and publishing.
"""

from shorts_engine.services.worker.runner import WorkerRunner, claim_next_clip, clear_cuda_cache

def __getattr__(name):
    if name == "WorkerDaemon":
        from shorts_engine.services.worker.daemon import WorkerDaemon
        return WorkerDaemon
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["WorkerRunner", "WorkerDaemon", "claim_next_clip", "clear_cuda_cache"]
