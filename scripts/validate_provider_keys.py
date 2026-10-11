"""
Non-destructive Provider Key Validation Probe.
Tests currently configured API keys against live upstream provider endpoints
to verify whether newly rotated credentials are active and functional,
or whether any keys are invalid / revoked.
Strictly masks all credentials so no plain-text secret is ever printed or logged.
"""

import sys
import time
import json
from pathlib import Path
import httpx
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import reload_runtime_settings
from shorts_engine.services.llm.quota_tracker import mask_api_key
from shorts_engine.security.secretbox import decrypt
from shorts_engine.db.session import SessionLocal
from shorts_engine.db.models import Channel

console = Console()


def probe_groq(key: str):
    t0 = time.time()
    try:
        res = httpx.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=8.0,
        )
        latency_ms = int((time.time() - t0) * 1000)
        if res.status_code == 200:
            return True, f"HTTP 200 OK ({latency_ms}ms)"
        elif res.status_code == 401:
            return False, "HTTP 401 Unauthorized (Revoked / Invalid)"
        else:
            return False, f"HTTP {res.status_code}: {res.text[:60]}"
    except Exception as e:
        return False, f"Connection error: {str(e)[:60]}"


def probe_gemini(key: str):
    t0 = time.time()
    try:
        res = httpx.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=8.0,
        )
        latency_ms = int((time.time() - t0) * 1000)
        if res.status_code == 200:
            return True, f"HTTP 200 OK ({latency_ms}ms)"
        elif res.status_code in (400, 403):
            return False, f"HTTP {res.status_code} Forbidden (Revoked / Invalid Key)"
        else:
            return False, f"HTTP {res.status_code}: {res.text[:60]}"
    except Exception as e:
        return False, f"Connection error: {str(e)[:60]}"


def probe_openrouter(key: str):
    t0 = time.time()
    try:
        res = httpx.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=8.0,
        )
        latency_ms = int((time.time() - t0) * 1000)
        if res.status_code == 200:
            return True, f"HTTP 200 OK ({latency_ms}ms)"
        elif res.status_code == 401:
            return False, "HTTP 401 Unauthorized (Revoked / Invalid)"
        else:
            return False, f"HTTP {res.status_code}: {res.text[:60]}"
    except Exception as e:
        return False, f"Connection error: {str(e)[:60]}"


def probe_openai(key: str):
    t0 = time.time()
    try:
        res = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=8.0,
        )
        latency_ms = int((time.time() - t0) * 1000)
        if res.status_code == 200:
            return True, f"HTTP 200 OK ({latency_ms}ms)"
        elif res.status_code == 401:
            return False, "HTTP 401 Unauthorized (Revoked / Invalid)"
        else:
            return False, f"HTTP {res.status_code}: {res.text[:60]}"
    except Exception as e:
        return False, f"Connection error: {str(e)[:60]}"


def probe_google_oauth_file():
    enc_path = settings.STORAGE_DIR / "client_secret.enc"
    if not enc_path.exists():
        return False, "storage/client_secret.enc missing"

    try:
        cipher = enc_path.read_text(encoding="utf-8").strip()
        plain = decrypt(cipher)
        data = json.loads(plain)
        # Verify structure
        cfg = data.get("installed") or data.get("web") or {}
        client_id = cfg.get("client_id", "")
        if client_id and "apps.googleusercontent.com" in client_id:
            masked_id = f"{client_id[:12]}...{client_id[-20:]}"
            return True, f"Encrypted & Valid ({masked_id})"
        return True, "Encrypted payload decrypted successfully"
    except Exception as e:
        return False, f"Decryption failure: {str(e)[:60]}"


def probe_channel_tokens():
    try:
        with SessionLocal() as db:
            channels = db.query(Channel).all()
            if not channels:
                return True, "No channels registered in database yet"
            valid_count = 0
            for ch in channels:
                if ch.encrypted_oauth_token:
                    try:
                        dec = decrypt(ch.encrypted_oauth_token)
                        if dec:
                            valid_count += 1
                    except Exception:
                        pass
            return True, f"{valid_count}/{len(channels)} channel token(s) decryptable"
    except Exception as e:
        return False, f"Database token query error: {str(e)[:60]}"


def main():
    reload_runtime_settings()
    console.print("\n[bold cyan]-- Running Non-Destructive Live Provider Key Validation Probe --[/bold cyan]\n")

    table = Table(title="Provider Credential Verification", header_style="bold magenta")
    table.add_column("Provider / System", style="cyan", width=22)
    table.add_column("Masked Key / ID", style="bold white", width=26)
    table.add_column("Live Status", width=18)
    table.add_column("Details / Response", style="dim", width=42)

    # 1. Groq Keys
    groq_keys = settings.get_groq_keys()
    if not groq_keys:
        table.add_row("Groq LPU", "-", "[yellow]NOT CONFIGURED[/yellow]", "No keys configured in .env or settings")
    else:
        for k in groq_keys:
            active, msg = probe_groq(k)
            status_badge = "[bold green][ACTIVE][/bold green]" if active else "[bold red][INVALID/REVOKED][/bold red]"
            table.add_row("Groq LPU", mask_api_key(k), status_badge, msg)

    # 2. Google Gemini Keys
    gemini_keys = settings.get_gemini_keys()
    if not gemini_keys:
        table.add_row("Google Gemini", "-", "[yellow]NOT CONFIGURED[/yellow]", "No keys configured in .env or settings")
    else:
        for k in gemini_keys:
            active, msg = probe_gemini(k)
            status_badge = "[bold green][ACTIVE][/bold green]" if active else "[bold red][INVALID/REVOKED][/bold red]"
            table.add_row("Google Gemini", mask_api_key(k), status_badge, msg)

    # 3. OpenRouter Keys
    openrouter_keys = settings.get_openrouter_keys()
    if not openrouter_keys:
        table.add_row("OpenRouter", "-", "[yellow]NOT CONFIGURED[/yellow]", "No keys configured in .env or settings")
    else:
        for k in openrouter_keys:
            active, msg = probe_openrouter(k)
            status_badge = "[bold green][ACTIVE][/bold green]" if active else "[bold red][INVALID/REVOKED][/bold red]"
            table.add_row("OpenRouter", mask_api_key(k), status_badge, msg)

    # 4. OpenAI Keys
    openai_keys = settings.get_openai_keys()
    if not openai_keys:
        table.add_row("OpenAI", "-", "[yellow]NOT CONFIGURED[/yellow]", "No keys configured in .env or settings")
    else:
        for k in openai_keys:
            active, msg = probe_openai(k)
            status_badge = "[bold green][ACTIVE][/bold green]" if active else "[bold red][INVALID/REVOKED][/bold red]"
            table.add_row("OpenAI", mask_api_key(k), status_badge, msg)

    # 5. Google OAuth Client Secret
    oauth_ok, oauth_msg = probe_google_oauth_file()
    oauth_badge = "[bold green][ACTIVE][/bold green]" if oauth_ok else "[bold red][ERROR][/bold red]"
    table.add_row("Google OAuth Secret", "storage/client_secret.enc", oauth_badge, oauth_msg)

    # 6. Channel OAuth Tokens
    chan_ok, chan_msg = probe_channel_tokens()
    chan_badge = "[bold green][ACTIVE][/bold green]" if chan_ok else "[bold red][ERROR][/bold red]"
    table.add_row("Channel OAuth Tokens", "DB: channels", chan_badge, chan_msg)

    console.print(table)
    console.print()


if __name__ == "__main__":
    main()
