import json
import sys
sys.stdout.reconfigure(encoding='utf-8')
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import SourceVideo, SourceVideoStatus, Clip, ClipStatus, ClipCandidate, Channel

def diagnose():
    with SessionLocal() as db:
        print("=== SOURCE VIDEOS INVENTORY ===")
        all_sources = db.query(SourceVideo).all()
        print(f"Total Source Videos: {len(all_sources)}")
        for sv in all_sources:
            ch_name = sv.channel.name if sv.channel else "Unknown Channel"
            print(f"[{sv.status.value}] {sv.id} | Channel: {ch_name} | YT ID: {sv.youtube_video_id}")
            print(f"  Title: {sv.title}")
            print(f"  Downloaded Path: {sv.downloaded_path} (exists: {bool(sv.downloaded_path)})")
            print(f"  Retry count: {sv.retry_count} / max")
            print(f"  Error message: {sv.error_message}")
            print(f"  Last Updated: {sv.updated_at}")
            print()

        print("=== FAILED CLIPS INVENTORY ===")
        failed_clips = db.query(Clip).filter(Clip.status == ClipStatus.FAILED).all()
        print(f"Total Failed Clips: {len(failed_clips)}")
        for c in failed_clips:
            cand = c.candidate
            sv = cand.source_video if cand else None
            sv_title = sv.title if sv else "Unknown Video"
            print(f"Clip ID: {c.id}")
            print(f"  Parent Video: {sv_title} (ID: {sv.id if sv else 'None'})")
            print(f"  Rendered Path: {c.rendered_path}")
            print(f"  Retry Count: {c.retry_count}")
            print(f"  Quality Check Result: {c.quality_check_result}")
            print(f"  Title: {c.generated_title}")
            print()

        print("=== CLIPS SUMMARY BY STATUS ===")
        for st in ClipStatus:
            cnt = db.query(Clip).filter(Clip.status == st).count()
            if cnt > 0:
                print(f"  {st.value}: {cnt}")

        print("\n=== DEAD-LETTER LOGS / ROUTE INSPECTION ===")
        # Check if there are any other error logs or dead-letter queues
        try:
            from shorts_engine.db.models import PublishLog
            failed_pubs = db.query(PublishLog).filter(PublishLog.status == "FAILED").all()
            print(f"Failed PublishLogs: {len(failed_pubs)}")
            for pl in failed_pubs:
                print(f"  Clip {pl.clip_id}: error={pl.error_message}")
        except Exception as e:
            print(f"PublishLog check exception: {e}")

if __name__ == "__main__":
    diagnose()
