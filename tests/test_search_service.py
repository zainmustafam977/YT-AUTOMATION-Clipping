"""Tests for E-021 search + command palette service."""
import sqlite3
import pytest

from shorts_engine.services.search import search_all, command_palette, _score


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            status TEXT
        );
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            handle TEXT
        );
        CREATE TABLE source_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            youtube_video_id TEXT
        );
        CREATE TABLE saved_views (
            id TEXT PRIMARY KEY,
            name TEXT,
            expression TEXT
        );
        CREATE TABLE overlay_preset (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO clips (title, status) VALUES (?, ?)",
        [
            ("How to bake sourdough", "ready"),
            ("Sourdough for beginners", "draft"),
            ("Top 10 cooking knives", "ready"),
            ("Knife sharpening tutorial", "ready"),
        ],
    )
    conn.executemany(
        "INSERT INTO channels (title, handle) VALUES (?, ?)",
        [
            ("Cooking with AI", "@cookingai"),
            ("Knife Master", "@knifemaster"),
        ],
    )
    conn.executemany(
        "INSERT INTO source_videos (title, youtube_video_id) VALUES (?, ?)",
        [
            ("Sourdough v2", "yt_001"),
            ("Knife skills 101", "yt_002"),
        ],
    )
    conn.executemany(
        "INSERT INTO saved_views (id, name, expression) VALUES (?, ?, ?)",
        [
            ("v1", "ready-clips", "status=ready"),
            ("v2", "all-knife", "title~knife"),
        ],
    )
    conn.executemany(
        "INSERT INTO overlay_preset (name, description) VALUES (?, ?)",
        [("lower-third", "name + handle"), ("title-card", "big text")],
    )
    conn.commit()
    yield conn
    conn.close()


def test_score_exact_match():
    assert _score("sourdough", "sourdough") == 100.0


def test_score_prefix():
    assert _score("sour", "sourdough") == 50.0


def test_score_substring():
    assert _score("dough", "sourdough") == 25.0


def test_score_word_prefix():
    # "ing" appears mid-word but not at start — substring match.
    assert _score("ing", "cooking with ai") == 25.0
    # "ai" is the last word — substring match.
    assert _score("ai", "cooking with ai") == 25.0
    # No match.
    assert _score("xyz", "cooking with ai") == 0.0


def test_score_no_match():
    assert _score("xyz", "sourdough") == 0.0


def test_search_empty_query_returns_nothing(db):
    assert search_all(db, "") == []
    assert search_all(db, "   ") == []


def test_search_finds_clips(db):
    results = search_all(db, "sourdough")
    clip_results = [r for r in results if r["kind"] == "clip"]
    assert len(clip_results) == 2


def test_search_finds_channels(db):
    results = search_all(db, "cooking")
    kinds = {r["kind"] for r in results}
    assert "channel" in kinds


def test_search_finds_source_videos(db):
    results = search_all(db, "yt_001")
    assert any(r["kind"] == "source_video" for r in results)


def test_search_finds_saved_views(db):
    results = search_all(db, "ready")
    kinds = {r["kind"] for r in results}
    assert "saved_view" in kinds


def test_search_finds_overlay_presets(db):
    results = search_all(db, "lower")
    assert any(r["kind"] == "overlay_preset" for r in results)


def test_search_results_sorted_by_score(db):
    results = search_all(db, "sourdough")
    assert results[0]["score"] >= results[-1]["score"]


def test_search_respects_limit_per_kind(db):
    # "ready" matches 3 clips and 1 saved view.
    results = search_all(db, "ready", limit_per_kind=1)
    clip_count = sum(1 for r in results if r["kind"] == "clip")
    assert clip_count <= 1


def test_search_includes_label_and_snippet(db):
    results = search_all(db, "sourdough")
    assert all("label" in r for r in results)
    assert all("snippet" in r for r in results)


def test_search_handles_missing_table(db):
    """Should not crash if a target table doesn't exist."""
    # Drop one table.
    db.execute("DROP TABLE saved_views")
    db.commit()
    results = search_all(db, "ready")  # would have matched saved_views + clips
    kinds = {r["kind"] for r in results}
    assert "saved_view" not in kinds


def test_command_palette_adds_action(db):
    results = command_palette(db, "sourdough")
    for r in results:
        assert "action" in r
        assert r["action"].startswith("/")


def test_command_palette_clip_action_format(db):
    results = command_palette(db, "sourdough")
    clip_results = [r for r in results if r["kind"] == "clip"]
    for r in clip_results:
        assert r["action"].startswith("/clips/")
        assert r["id"] is not None


def test_command_palette_no_match(db):
    results = command_palette(db, "xyznotfound")
    assert results == []


def test_search_score_exact_beats_substring(db):
    """Exact match should rank higher than substring match."""
    db2 = sqlite3.connect(":memory:")
    db2.execute("CREATE TABLE clips (id INTEGER PRIMARY KEY, title TEXT)")
    db2.executemany(
        "INSERT INTO clips (title) VALUES (?)",
        [("foo",), ("foobar",), ("x foo y",)],
    )
    db2.commit()
    results = search_all(db2, "foo")
    assert results[0]["label"] == "foo"
    db2.close()
