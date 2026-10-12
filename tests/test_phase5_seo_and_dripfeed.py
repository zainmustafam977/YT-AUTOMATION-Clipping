"""
Phase 5: SEO Engine, Drip-Feed Idempotency & Dynamic Config Store Tests
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from shorts_engine.services.seo.engine import SEOMetadataEngine, SEOResult
from shorts_engine.services.publishing.metadata import extract_clip_id_from_description, check_duplicate_upload
from shorts_engine.config.dynamic_store import DynamicConfigStore
from shorts_engine.db.models import Clip

@pytest.fixture
def mock_db_session():
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

# SEO Engine Tests (min 8 tests)
def test_seo_result_dataclass():
    res = SEOResult(
        title_a="A",
        title_b="B",
        title_c="C",
        description="Desc",
        tags=["#t1"]
    )
    assert res.title_a == "A"
    assert res.title_b == "B"
    assert res.title_c == "C"
    assert res.description == "Desc"
    assert res.tags == ["#t1"]

class MockRouter:
    def generate_structured(self, *args, **kwargs):
        raise Exception("LLM offline")

def test_seo_engine_fallback_no_llm(mock_db_session):
    engine = SEOMetadataEngine(router=MockRouter())
    clip = Clip(id="123")
    res = engine.generate_seo_metadata(clip, mock_db_session)
    assert "Viral Short" in res.description
    assert "[c:123]" in res.description

def test_seo_engine_title_max_length(mock_db_session):
    engine = SEOMetadataEngine(router=MockRouter())
    clip = Clip(id="123")
    res = engine.generate_seo_metadata(clip, mock_db_session)
    assert len(res.title_a) <= 100
    assert len(res.title_b) <= 100
    assert len(res.title_c) <= 100

def test_seo_engine_description_contains_token(mock_db_session):
    engine = SEOMetadataEngine(router=MockRouter())
    clip = Clip(id="abc")
    res = engine.generate_seo_metadata(clip, mock_db_session)
    assert "[c:abc]" in res.description

def test_seo_engine_tags_count(mock_db_session):
    engine = SEOMetadataEngine(router=MockRouter())
    clip = Clip(id="123")
    res = engine.generate_seo_metadata(clip, mock_db_session)
    assert 10 <= len(res.tags) <= 25

def test_select_best_title_curiosity_gap():
    engine = SEOMetadataEngine(router=MockRouter())
    res = SEOResult(title_a="A", title_b="B", title_c="C", description="Desc", tags=[])
    assert engine.select_best_title(res, "curiosity_gap") == "A"

def test_select_best_title_shock_factor():
    engine = SEOMetadataEngine(router=MockRouter())
    res = SEOResult(title_a="A", title_b="B", title_c="C", description="Desc", tags=[])
    assert engine.select_best_title(res, "shock_factor") == "B"

def test_seo_engine_shorts_tag_appended(mock_db_session):
    engine = SEOMetadataEngine(router=MockRouter())
    clip = Clip(id="123")
    res = engine.generate_seo_metadata(clip, mock_db_session)
    assert "#Shorts" in res.title_a or "#shorts" in res.title_a
    assert "#Shorts" in res.title_b or "#shorts" in res.title_b
    assert "#Shorts" in res.title_c or "#shorts" in res.title_c

# Drip-Feed Idempotency Tests (min 4 tests)
def test_extract_clip_id_from_description_found():
    assert extract_clip_id_from_description("Some text [c:abc123] more text") == "abc123"

def test_extract_clip_id_from_description_not_found():
    assert extract_clip_id_from_description("Some text without token") is None

def test_check_duplicate_upload_true():
    assert check_duplicate_upload("abc123", ["desc 1", "desc with [c:abc123] token"]) is True

def test_check_duplicate_upload_false():
    assert check_duplicate_upload("abc123", ["desc 1", "desc with no token"]) is False

# Dynamic Config Store Tests (min 4 tests)
def test_dynamic_config_store_singleton():
    store1 = DynamicConfigStore.get_instance()
    store2 = DynamicConfigStore.get_instance()
    assert store1 is store2

def test_dynamic_config_store_set_get(mock_db_session):
    store = DynamicConfigStore()
    store.set("test_key", "test_val", mock_db_session)
    assert store.get("test_key") == "test_val"

def test_dynamic_config_store_load_from_db_no_table(mock_db_session):
    store = DynamicConfigStore()
    # table doesn't exist, should not crash
    store.load_from_db(mock_db_session)

def test_dynamic_config_store_to_dict():
    store = DynamicConfigStore()
    store._cache = {"a": 1, "b": "2"}
    assert store.to_dict() == {"a": 1, "b": "2"}
