from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import SourceVideo

def update_titles():
    with SessionLocal() as db:
        v1 = db.query(SourceVideo).filter_by(youtube_video_id='yr7VY7_oYW4').first()
        if v1:
            v1.title = "The Case for the Death Penalty"
            print(f"Updated {v1.youtube_video_id} -> {v1.title}")

        v2 = db.query(SourceVideo).filter_by(youtube_video_id='B3MNoF2lNtY').first()
        if v2:
            v2.title = "The Hijacking Story Just Got More Insane. Here's The Latest."
            print(f"Updated {v2.youtube_video_id} -> {v2.title}")

        db.commit()

if __name__ == "__main__":
    update_titles()
