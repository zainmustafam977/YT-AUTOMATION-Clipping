"""
CLI Interface for the YouTube Shorts Automation Platform.
Provides commands for database initialization, channel management,
video processing, status checks, RSS watch loop, dashboard server,
and WebSub subscription management.
"""

import os
import sys
import json
import getpass
import logging
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

# Add root directory to pythonpath
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import setup_logging, reload_runtime_settings
from shorts_engine.db.session import init_db, SessionLocal
from shorts_engine.db.models import Channel, SourceVideo, Clip, ClipStatus, PublishLog
from shorts_engine.services.publishing.quota import QuotaTracker
from shorts_engine.orchestration.runner import PipelineRunner

app = typer.Typer(help="YouTube Shorts Automation CLI")
channel_app = typer.Typer(help="Manage watched YouTube channels")
app.add_typer(channel_app, name="channel")
console = Console()

setup_logging()
reload_runtime_settings()


@app.command("init-db")
def cmd_init_db():
    """Initializes the database schema."""
    console.print("[bold cyan]Initializing database schema...[/bold cyan]")
    init_db()
    console.print("[bold green]Database initialized successfully![/bold green]")


@app.command("rotate-secrets")
def cmd_rotate_secrets(
    old_key: Optional[str] = typer.Option(None, "--old-key", help="Current master encryption key (prefer interactive prompt or OLD_MASTER_KEY env var)"),
    new_key: Optional[str] = typer.Option(None, "--new-key", help="New master encryption key (prefer interactive prompt or NEW_MASTER_KEY env var)"),
):
    """
    Bulk re-encrypts all stored channel OAuth tokens under a new master key
    in a single atomic transaction. Automatically creates a database backup before rotating.
    Rolls back cleanly if any token fails.
    """
    import os
    from shorts_engine.db.security import rotate_all_channel_tokens

    actual_old_key = (
        old_key
        or os.getenv("OLD_MASTER_KEY")
        or os.getenv("ROTATION_OLD_KEY")
    )
    if not actual_old_key:
        actual_old_key = typer.prompt("Enter current master encryption key", hide_input=True)
    elif old_key:
        console.print("[yellow]SECURITY WARNING: Passing --old-key via CLI exposes secrets to shell history and process tables. Use interactive prompt or environment variables instead.[/yellow]")

    actual_new_key = (
        new_key
        or os.getenv("NEW_MASTER_KEY")
        or os.getenv("ROTATION_NEW_KEY")
    )
    if not actual_new_key:
        actual_new_key = typer.prompt("Enter new master encryption key", hide_input=True)
    elif new_key:
        console.print("[yellow]SECURITY WARNING: Passing --new-key via CLI exposes secrets to shell history and process tables. Use interactive prompt or environment variables instead.[/yellow]")

    if not actual_old_key or not actual_new_key:
        console.print("[bold red]Both old and new master keys are required for secret rotation.[/bold red]")
        raise typer.Exit(1)

    init_db()
    db = SessionLocal()
    try:
        console.print("[bold cyan]Initiating bulk secret rotation across all channels (with automated backup)...[/bold cyan]")
        count = rotate_all_channel_tokens(db, old_master_key=actual_old_key, new_master_key=actual_new_key, auto_backup=True)
        console.print(f"[bold green]Successfully rotated {count} stored secret(s) in a single atomic transaction![/bold green]")
        console.print("[yellow]Update MASTER_ENCRYPTION_KEY in your .env / secret manager to the new key.[/yellow]")
    except Exception as e:
        console.print(f"[bold red]Secret rotation failed! Transaction rolled back: {e}[/bold red]")
        raise typer.Exit(1)
    finally:
        db.close()



@app.command("backup-db")
def cmd_backup_db(
    no_compress: bool = typer.Option(False, "--no-compress", help="Do not gzip compress the backup archive"),
):
    """Creates an atomic online hot backup of the database without locking."""
    from shorts_engine.services.storage.backup_manager import DatabaseBackupManager
    console.print("[bold cyan]Initiating atomic hot database backup...[/bold cyan]")
    manager = DatabaseBackupManager()
    try:
        meta = manager.create_backup(compress=(not no_compress))
        console.print("[bold green]Database backup created successfully![/bold green]")
        console.print(f"  File:     {meta['filename']}")
        console.print(f"  Size:     {meta['size_mb']} MB")
        console.print(f"  SHA-256:  {meta['sha256']}")
        console.print(f"  Duration: {meta['duration_sec']}s")
        if meta.get("purged_old_backups", 0) > 0:
            console.print(f"  Retention: Purged {meta['purged_old_backups']} expired backup(s).")
    except Exception as e:
        console.print(f"[bold red]Backup failed: {e}[/bold red]")
        raise typer.Exit(1)


@app.command("list-backups")
def cmd_list_backups():
    """Lists all available database backup archives."""
    from shorts_engine.services.storage.backup_manager import DatabaseBackupManager
    manager = DatabaseBackupManager()
    backups = manager.list_backups()
    if not backups:
        console.print("[yellow]No database backups found in storage/backups/.[/yellow]")
        return
    table = Table(title="Database Backups")
    table.add_column("Filename", style="cyan")
    table.add_column("Size", style="green")
    table.add_column("Created At", style="yellow")
    for b in backups:
        table.add_row(b["filename"], f"{b['size_mb']} MB", b["created_at"])
    console.print(table)


@app.command("restore-db")
def cmd_restore_db(
    file: str = typer.Option(..., "--file", "-f", help="Path to backup file (.db or .db.gz)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm overwrite without interactive prompt"),
):
    """Restores an SQLite database from a hot backup archive."""
    from shorts_engine.services.storage.backup_manager import DatabaseBackupManager
    p = Path(file)
    if not p.exists():
        console.print(f"[bold red]Backup file '{file}' not found![/bold red]")
        raise typer.Exit(1)

    if not yes:
        confirm = typer.confirm(f"Are you sure you want to restore from '{p.name}'? Current database will be snapshotted.")
        if not confirm:
            console.print("[yellow]Restoration cancelled.[/yellow]")
            return

    manager = DatabaseBackupManager()
    try:
        manager.restore_backup(p)
        console.print(f"[bold green]Database restored successfully from {p.name}![/bold green]")
    except Exception as e:
        console.print(f"[bold red]Restore failed: {e}[/bold red]")
        raise typer.Exit(1)


@channel_app.command("add")
def cmd_channel_add(
    channel_id: str = typer.Option(..., "--channel-id", "-c", help="YouTube Channel ID (e.g. UC...)"),
    name: str = typer.Option(..., "--name", "-n", help="Channel display name"),
    auto_publish: bool = typer.Option(False, "--auto-publish", help="Enable automatic publishing without human review"),
):
    """Adds a new YouTube channel to watch."""
    db = SessionLocal()
    try:
        existing = db.query(Channel).filter_by(youtube_channel_id=channel_id).first()
        if existing:
            console.print(f"[yellow]Channel '{name}' ({channel_id}) already exists![/yellow]")
            return

        channel = Channel(
            youtube_channel_id=channel_id,
            name=name,
            watch_enabled=True,
            human_review_required=(not auto_publish),
        )
        db.add(channel)
        db.commit()
        console.print(f"[bold green]Channel '{name}' ({channel_id}) added successfully![/bold green]")
    finally:
        db.close()


@channel_app.command("list")
def cmd_channel_list():
    """Lists all configured YouTube channels."""
    db = SessionLocal()
    try:
        channels = db.query(Channel).all()
        table = Table(title="Watched YouTube Channels")
        table.add_column("Channel ID", style="cyan")
        table.add_column("Name", style="bold white")
        table.add_column("Watch Enabled", style="green")
        table.add_column("Human Review", style="magenta")
        table.add_column("Last Seen Video", style="yellow")

        for ch in channels:
            table.add_row(
                ch.youtube_channel_id,
                ch.name,
                "YES" if ch.watch_enabled else "NO",
                "REQUIRED" if ch.human_review_required else "AUTO-PUBLISH",
                ch.last_seen_video_id or "<none>",
            )
        console.print(table)
    finally:
        db.close()


@channel_app.command("remove")
def cmd_channel_remove(
    channel_id: str = typer.Option(..., "--channel-id", "-c", help="YouTube Channel ID (UC...) or internal UUID"),
):
    """Removes a configured YouTube channel and its associated data."""
    db = SessionLocal()
    try:
        channel = (
            db.query(Channel)
            .filter((Channel.youtube_channel_id == channel_id) | (Channel.id == channel_id))
            .first()
        )
        if not channel:
            console.print(f"[bold red]Channel '{channel_id}' not found![/bold red]")
            raise typer.Exit(1)
        name = channel.name
        db.delete(channel)
        db.commit()
        console.print(f"[bold green]Channel '{name}' ({channel_id}) removed successfully![/bold green]")
    finally:
        db.close()


@app.command("process")
def cmd_process(
    video: str = typer.Option(..., "--video", "-v", help="YouTube video URL or Video ID"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Simulate upload without pushing live"),
):
    """Runs the complete shorts automation pipeline on a source video."""
    init_db()
    db = SessionLocal()
    try:
        console.print(f"[bold cyan]Launching pipeline for: {video} (dry_run={dry_run})...[/bold cyan]")
        runner = PipelineRunner(db)
        result = runner.run_pipeline_for_video(video, dry_run=dry_run)

        console.print("\n[bold green]Pipeline completed successfully![/bold green]")
        console.print(f"Title: {result['title']}")
        console.print(f"Clips Generated: {result['clips_count']}")
        for c in result.get("clips", []):
            rprint(c)
    finally:
        db.close()


@app.command("resume")
def cmd_resume(
    video: str = typer.Option(..., "--video", "-v", help="YouTube video URL or Video ID to resume"),
    dry_run: bool = typer.Option(False, "--dry-run/--live", help="Simulate upload without pushing live"),
):
    """Resumes an interrupted or stalled video processing run from its last checkpoint."""
    init_db()
    db = SessionLocal()
    try:
        console.print(f"[bold cyan]Resuming pipeline for: {video} (dry_run={dry_run})...[/bold cyan]")
        runner = PipelineRunner(db)
        result = runner.resume_pipeline_for_video(video, dry_run=dry_run)

        console.print(f"\n[bold green]Pipeline resumed with status: {result['status']}![/bold green]")
        console.print(f"Title: {result['title']}")
        console.print(f"Clips: {result['clips_count']}")
        for c in result.get("clips", []):
            rprint(c)
    finally:
        db.close()


@app.command("reconcile-stale")
def cmd_reconcile_stale():
    """Scans and unlocks clips stuck in dead worker claims and cleans old temp files."""
    from shorts_engine.services.worker.runner import reconcile_stale_claims
    from shorts_engine.services.ingestion.downloader import IngestionService
    init_db()
    db = SessionLocal()
    try:
        count = reconcile_stale_claims(db)
        purged = IngestionService.purge_stale_temp_files(max_age_hours=24.0)
        console.print(f"[bold green]Reconciled {count} stale clip claim(s).[/bold green]")
        console.print(f"[bold green]Purged {purged} stale temp file(s).[/bold green]")
    finally:
        db.close()


@app.command("status")
def cmd_status():
    """Displays system status, pending review queue, and daily API quota."""
    init_db()
    db = SessionLocal()
    try:
        total_videos = db.query(SourceVideo).count()
        total_clips = db.query(Clip).count()
        waiting_review = db.query(Clip).filter_by(status=ClipStatus.WAITING_APPROVAL).count()
        published = db.query(Clip).filter_by(status=ClipStatus.PUBLISHED).count()
        failed = db.query(Clip).filter_by(status=ClipStatus.FAILED).count()

        tracker = QuotaTracker()
        can_pub, remaining, reason = tracker.can_publish()

        table = Table(title="YouTube Shorts Automation Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold green")

        table.add_row("Total Source Videos Ingested", str(total_videos))
        table.add_row("Total Clips Generated", str(total_clips))
        table.add_row("Clips Awaiting Human Approval", f"[yellow]{waiting_review}[/yellow]")
        table.add_row("Clips Live / Published", str(published))
        table.add_row("Failed Clips", f"[red]{failed}[/red]" if failed else "0")
        table.add_row("YouTube API Quota Remaining", f"{remaining}/{settings.DAILY_QUOTA_LIMIT} units")

        console.print(table)
    finally:
        db.close()


@app.command("watch")
def cmd_watch(
    interval: int = typer.Option(
        None, "--interval", "-i",
        help="Poll interval in minutes (default: RSS_POLL_INTERVAL_MINUTES from config)"
    ),
):
    """
    Starts the RSS fallback poll loop. Watches all enabled channels for new uploads
    and queues them for pipeline processing. Blocking — run in a tmux/screen session or as a service.
    """
    init_db()
    console.print("[bold cyan]Starting RSS watch loop...[/bold cyan]")
    console.print(f"  Interval: {interval or settings.RSS_POLL_INTERVAL_MINUTES} minutes")
    console.print(f"  Press Ctrl+C to stop.\n")

    from shorts_engine.services.detection.detector import DetectionService
    from shorts_engine.services.monitoring.alerter import PipelineAlerter

    alerter = PipelineAlerter()

    def on_new_video(channel_id: str, video_id: str):
        console.print(f"[green]Detected new video[/green] {video_id} on channel {channel_id}")
        db = SessionLocal()
        try:
            detection = DetectionService(db)
            detection.on_new_video(channel_id, video_id)
        except Exception as e:
            alerter.alert("Detection Error", str(e), level="ERROR",
                          context={"channel_id": channel_id, "video_id": video_id})
        finally:
            db.close()

    from shorts_engine.services.detection.rss_poller import RSSPollLoop
    loop = RSSPollLoop(SessionLocal, on_new_video)
    try:
        loop.run(interval_minutes=interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch loop stopped.[/yellow]")


@app.command("dashboard")
def cmd_dashboard(
    host: str = typer.Option(None, "--host", help="Bind host (default: DASHBOARD_HOST from config)"),
    port: int = typer.Option(None, "--port", "-p", help="Bind port (default: DASHBOARD_PORT from config)"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development"),
):
    """
    Starts the operator dashboard web server.
    Access at http://<host>:<port>/ — protected by Session Cookie & RBAC Auth.
    Use 'python run.py create-user' to bootstrap initial admin accounts.
    """
    import uvicorn
    init_db()
    bind_host = host or settings.DASHBOARD_HOST
    bind_port = port or settings.DASHBOARD_PORT
    console.print(f"[bold cyan]Starting dashboard at http://{bind_host}:{bind_port}/[/bold cyan]")
    console.print("  Auth: Multi-User RBAC (Session Cookies / Bearer Tokens)")
    console.print("  Bootstrap Admin: python run.py create-user --username admin --password <SECRET> --role admin\n")
    uvicorn.run(
        "shorts_engine.dashboard.app:app",
        host=bind_host,
        port=bind_port,
        reload=reload,
        log_level="info",
    )


@app.command("subscribe")
def cmd_subscribe(
    channel_id: str = typer.Option(..., "--channel-id", "-c", help="YouTube Channel ID to subscribe to"),
):
    """
    Registers a WebSub (PubSubHubbub) subscription for a channel.
    Requires WEBSUB_CALLBACK_URL to be set in config/.env.
    """
    from shorts_engine.services.detection.websub import WebSubSubscriber

    if not settings.WEBSUB_CALLBACK_URL:
        console.print("[bold red]Error: WEBSUB_CALLBACK_URL is not configured.[/bold red]")
        console.print("Set it in your .env file to a publicly reachable URL.")
        raise typer.Exit(1)

    subscriber = WebSubSubscriber()
    console.print(f"[cyan]Subscribing channel {channel_id} to WebSub hub...[/cyan]")
    success = subscriber.subscribe(channel_id)

    if success:
        console.print(f"[bold green]WebSub subscription requested for {channel_id}.[/bold green]")
        console.print(f"YouTube will verify by sending a GET to {settings.WEBSUB_CALLBACK_URL}?hub.mode=subscribe&...")
    else:
        console.print(f"[bold red]WebSub subscription failed for {channel_id}. Check logs.[/bold red]")
        raise typer.Exit(1)


@app.command("create-user")
def cmd_create_user(
    username: str = typer.Option(..., "--username", "-u", help="Username for dashboard operator"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="DEPRECATED: Plaintext password (prefer interactive prompt or DASHBOARD_BOOTSTRAP_PASSWORD)"),
    role: str = typer.Option("operator", "--role", "-r", help="Role: admin, operator, or viewer"),
):
    """
    Creates a new user with PBKDF2 hashed password and multi-user RBAC role.
    Enforces minimum 12-character password length.
    Masks password input via getpass when omitted from CLI, and supports DASHBOARD_BOOTSTRAP_PASSWORD.
    """
    from shorts_engine.dashboard.auth import hash_password
    from shorts_engine.db.models import User, UserRole

    if password is not None:
        console.print("[yellow]WARNING: Passing --password on the command line is insecure and exposes credentials in process tables and shell history. Use interactive prompt or DASHBOARD_BOOTSTRAP_PASSWORD.[/yellow]")
    else:
        password = os.getenv("DASHBOARD_BOOTSTRAP_PASSWORD") or os.getenv("SHORTS_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("Enter password (minimum 12 characters): ")

    if not password or len(password) < 12:
        console.print("[bold red]Error: Password must be at least 12 characters long.[/bold red]")
        raise typer.Exit(1)

    role_str = role.lower()
    if role_str == "admin":
        user_role = UserRole.ADMIN
    elif role_str == "operator":
        user_role = UserRole.OPERATOR
    elif role_str == "viewer":
        user_role = UserRole.VIEWER
    else:
        console.print(f"[bold red]Error: Invalid role '{role}'. Must be admin, operator, or viewer.[/bold red]")
        raise typer.Exit(1)

    init_db()
    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(username=username).first()
        if existing:
            console.print(f"[yellow]User '{username}' already exists. Updating password and role.[/yellow]")
            existing.password_hash = hash_password(password)
            existing.role = user_role
            existing.is_active = True
        else:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=user_role,
                is_active=True,
            )
            db.add(user)
        db.commit()
        console.print(f"[bold green]User '{username}' ({role_str}) saved successfully![/bold green]")
    finally:
        db.close()


@app.command("worker")
def cmd_worker(
    concurrency: int = typer.Option(1, "--concurrency", "-c", help="Worker concurrency level"),
    poll_interval: float = typer.Option(5.0, "--poll-interval", help="Queue poll interval in seconds"),
):
    """
    Runs the headless background rendering and quality verification worker.
    Uses dialect-aware job locking (PostgreSQL FOR UPDATE SKIP LOCKED vs SQLite CAS)
    and flushes GPU VRAM between jobs.
    """
    from shorts_engine.services.worker.runner import WorkerRunner
    init_db()
    console.print(f"[bold cyan]Starting background worker (concurrency={concurrency}, poll={poll_interval}s)...[/bold cyan]")
    runner = WorkerRunner(SessionLocal)
    runner.run_loop(poll_interval=poll_interval)


if __name__ == "__main__":
    app()
