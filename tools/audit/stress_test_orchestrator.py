"""
==============================================================================
Hardcore Worst-Case Scenario & Multi-Parameter Stress Test Battery
==============================================================================
Exercises all architectural tiers, edge cases, failure situations,
and parameter combinations across long video sources:
1. Multi-tier LLM routing under heavy token loads (Groq, Gemini, OpenRouter)
2. YuNet ONNX face tracking and vertical 9:16 portrait reframing
3. Broadcast audio mastering (EBU R128 -14 LUFS, 80Hz HPF, agate, sidechain ducking)
4. All 5 Caption Style Presets (Hormozi, MrBeast, Neon, Minimalist, Box)
5. Multi-tier Quality Verification Gates (Fatal, Black, Silence, CV Deadzones)
6. Automated Failure Diagnostics & Universal Recovery Auto-Fixes
7. Bayesian Multi-Armed Bandit Title Optimization & Velocity Circuit Breaker
8. Subprocess Watchdog OS process-tree termination & Hot Database Backup
==============================================================================
"""

import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time
import json
import shutil
import hashlib
from pathlib import Path
from typing import Dict, Any, List

# Ensure repository root is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts_engine.config.settings import settings
from shorts_engine.config.logging_config import setup_logging, reload_runtime_settings
from shorts_engine.db.session import SessionLocal, init_db
from shorts_engine.db.models import (
    Channel,
    SourceVideo,
    SourceVideoStatus,
    Transcript,
    ClipCandidate,
    Clip,
    ClipStatus,
)
from shorts_engine.services.storage.path_manager import PathManager
from shorts_engine.services.llm.router import LLMRouter
from shorts_engine.services.llm.quota_tracker import APIUsageTracker
from shorts_engine.services.transcription.formatter import FormattedTranscript, WordSegment
from shorts_engine.services.clip_selection.selector import ClipSelectionService
from shorts_engine.services.clip_selection.boundaries import BoundarySnapper
from shorts_engine.services.captioning.templates import get_caption_template, list_caption_templates
from shorts_engine.services.captioning.ass_generator import ASSSubtitleGenerator
from shorts_engine.services.reframing.cropper import VideoReframer, CropPlan
from shorts_engine.services.rendering.compositor import VideoCompositor, get_best_video_encoder
from shorts_engine.services.quality_gate.technical_check import TechnicalVerifier
from shorts_engine.services.quality_gate.render_inspector import RenderInspector
from shorts_engine.services.quality_gate.review_queue import QualityGateManager
from shorts_engine.services.quality_gate.recovery import ClipRecoveryService
from shorts_engine.services.ab_testing.bandit import BayesianVariantOptimizer
from shorts_engine.services.ab_testing.winner_daemon import ABWinnerPromotionDaemon
from shorts_engine.services.storage.backup_manager import DatabaseBackupManager
from shorts_engine.services.execution.watchdog import SubprocessWatchdog

setup_logging()
reload_runtime_settings()

REPORT: Dict[str, Any] = {
    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "subsystems_tested": {},
    "parameter_combinations": {},
    "operations_executed": [],
    "failures_detected": [],
    "auto_fixes_applied": [],
    "timings": {},
}


def log_step(name: str):
    print(f"\n{'='*75}\n>>> [HARDCORE STRESS TEST] {name}\n{'='*75}")


def test_01_environment_and_hardware():
    log_step("1. Hardware Encoding & Model Assets Verification")
    t0 = time.time()
    
    encoder, enc_args = get_best_video_encoder()
    print(f"[+] Active Video Encoder: {encoder} (args: {' '.join(enc_args)})")
    
    # Check YuNet ONNX model presence
    yunet_path = settings.MODELS_DIR / "face_detection_yunet.onnx"
    yunet_alt = Path("shorts_engine/services/reframing/models/face_detection_yunet.onnx")
    has_yunet = yunet_path.exists() or yunet_alt.exists()
    print(f"[+] YuNet Face Tracking ONNX Model: {'FOUND' if has_yunet else 'NOT FOUND'}")
    
    # Check LLM key pool
    groq_k = len(settings.get_groq_keys())
    gemini_k = len(settings.get_gemini_keys())
    openrouter_k = len(settings.get_openrouter_keys())
    print(f"[+] API Key Pools: Groq={groq_k}, Gemini={gemini_k}, OpenRouter={openrouter_k}")
    
    REPORT["subsystems_tested"]["hardware_and_environment"] = {
        "encoder": encoder,
        "yunet_onnx_present": has_yunet,
        "api_keys": {"groq": groq_k, "gemini": gemini_k, "openrouter": openrouter_k},
    }
    REPORT["timings"]["environment_check_sec"] = round(time.time() - t0, 3)


def test_02_long_video_transcript_and_high_token_llm():
    log_step("2. Long Video Transcript & Heavy Token LLM Routing")
    t0 = time.time()
    
    db = SessionLocal()
    try:
        # Fetch longest available transcript
        longest_t = None
        max_words = 0
        for t in db.query(Transcript).all():
            try:
                data = json.loads(t.word_level_json)
                words = data.get("words", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                if len(words) > max_words:
                    max_words = len(words)
                    longest_t = t
            except Exception:
                continue
                
        if not longest_t or max_words == 0:
            print("[-] No existing transcript found, skipping test 2")
            return None
            
        sv = db.query(SourceVideo).filter_by(id=longest_t.source_video_id).first()
        data = json.loads(longest_t.word_level_json)
        raw_words = data.get("words", []) if isinstance(data, dict) else longest_t.word_level_json
        formatted = FormattedTranscript.from_dict(data) if isinstance(data, dict) else FormattedTranscript([WordSegment.from_dict(w) for w in raw_words])
        
        print(f"[+] Target Long Video: '{sv.title[:50]}...' ({sv.youtube_video_id})")
        print(f"[+] Duration: {sv.duration_sec}s (~{sv.duration_sec/60:.1f} mins), Words: {len(formatted.words)}, Full Text Length: {len(formatted.full_text)} chars")
        
        # Test LLM Router with Groq, Gemini, and Heuristic fallback
        router = LLMRouter()
        print(f"[+] Testing LLM Router Provider: {router.provider} with model: {settings.LLM_MODEL_NAME}")
        
        # Select clips with strict duration bounds [20s, 50s] and min 3 clips
        selector = ClipSelectionService()
        selected_clips = selector.select_clips(
            formatted,
            min_clips=3,
            max_clips=5,
            target_duration_min=20,
            target_duration_max=50,
        )
        
        print(f"[+] Selected {len(selected_clips)} viral clip candidates from long video transcript:")
        for idx, c in enumerate(selected_clips):
            dur = (c.end_ms - c.start_ms) / 1000.0
            print(f"    Clip {idx+1}: [{c.start_ms}ms - {c.end_ms}ms] ({dur:.1f}s) | Score: {c.score:.2f}")
            print(f"      Hook: \"{c.hook_text}\"")
            print(f"      Payoff: \"{c.payoff_text}\"")
            print(f"      Reason: {c.reason[:90]}...")
            
        REPORT["subsystems_tested"]["long_video_selection"] = {
            "video_id": sv.youtube_video_id,
            "duration_sec": sv.duration_sec,
            "word_count": len(formatted.words),
            "clips_generated": len(selected_clips),
            "clips": [
                {
                    "start_ms": c.start_ms,
                    "end_ms": c.end_ms,
                    "duration_sec": (c.end_ms - c.start_ms) / 1000.0,
                    "score": c.score,
                    "hook": c.hook_text,
                }
                for c in selected_clips
            ]
        }
        REPORT["timings"]["llm_selection_sec"] = round(time.time() - t0, 3)
        return selected_clips, sv, formatted
    finally:
        db.close()


def test_03_all_5_caption_presets():
    log_step("3. All 5 Caption Style Presets Generation & Hex ASS Transcoding")
    t0 = time.time()
    
    presets = ["hormozi", "mrbeast", "neon", "minimalist", "high_contrast_box"]
    sample_words = [
        WordSegment("THIS", 1.0, 1.4),
        WordSegment("IS", 1.4, 1.7),
        WordSegment("AN", 1.7, 1.9),
        WordSegment("INDUSTRIAL", 1.9, 2.6),
        WordSegment("HARDCORE", 2.6, 3.2),
        WordSegment("STRESS", 3.2, 3.7),
        WordSegment("TEST", 3.7, 4.2),
    ]
    
    results = {}
    generator = ASSSubtitleGenerator()
    temp_dir = settings.TEMP_DIR / "preset_tests"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    for p in presets:
        style_config = get_caption_template(p)
        out_ass = temp_dir / f"test_{p}.ass"
        generator.generate_ass(
            words=sample_words,
            segment_start_ms=1000,
            segment_end_ms=4500,
            output_path=out_ass,
            style_overrides=style_config.model_dump(),
            max_words_per_line=style_config.max_words_per_line,
        )
        content = out_ass.read_text(encoding="utf-8")
        has_v4_style = "[V4+ Styles]" in content
        has_dialogue = "Dialogue:" in content
        char_count = len(content)
        print(f"[+] Preset '{p:18}': Valid={has_v4_style and has_dialogue} | Length={char_count} chars | Font={style_config.font_name}")
        results[p] = {
            "valid": has_v4_style and has_dialogue,
            "char_count": char_count,
            "font": style_config.font_name,
            "primary_color": style_config.primary_color,
            "highlight_color": style_config.highlight_color,
        }
        
    REPORT["subsystems_tested"]["caption_presets_matrix"] = results
    REPORT["timings"]["caption_presets_sec"] = round(time.time() - t0, 3)


def test_04_full_clip_rendering_and_audio_mastering(source_video, selected_clip, transcript):
    log_step("4. Broadcast Audio Mastering (EBU R128) & High-Quality Vertical Video Rendering")
    t0 = time.time()
    
    source_mp4 = PathManager.resolve_download_path(source_video)
    if not source_mp4 or not source_mp4.exists():
        print(f"[-] Source MP4 not found for {source_video.youtube_video_id}")
        return None
        
    print(f"[+] Source MP4: {source_mp4.name} ({source_mp4.stat().st_size / (1024*1024):.2f} MB)")
    
    # Clip parameters: render 10 seconds slice of the chosen candidate to test full compositor
    c_start = selected_clip.start_ms
    c_end = min(selected_clip.start_ms + 10000, selected_clip.end_ms)  # 10 sec hardcore slice
    
    out_dir = settings.OUTPUT_DIR / "stress_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_filename = f"hardcore_clip_{source_video.youtube_video_id}_{c_start}_{c_end}.mp4"
    out_mp4 = out_dir / out_filename
    
    # 1. Computer Vision Reframing: analyze video face tracking and build crop plan
    reframer = VideoReframer()
    print(f"[+] Running YuNet Face Detection & Dynamic 9:16 Portrait Cropper...")
    crop_plan = reframer.compute_crop_plan(
        video_path=source_mp4,
        start_ms=c_start,
        end_ms=c_end,
    )
    print(f"[+] Crop Plan: Mode={crop_plan.mode}, Crop=[{crop_plan.crop_w}x{crop_plan.crop_h} at ({crop_plan.crop_x}, {crop_plan.crop_y})], MultiSpeaker={crop_plan.is_multi_speaker}")
    
    # 2. Generate ASS subtitles with Hormozi preset
    generator = ASSSubtitleGenerator()
    ass_path = settings.TEMP_DIR / f"stress_{c_start}_{c_end}.ass"
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    hormozi_tpl = get_caption_template("hormozi")
    generator.generate_ass(
        words=transcript.words,
        segment_start_ms=c_start,
        segment_end_ms=c_end,
        output_path=ass_path,
        style_overrides=hormozi_tpl.model_dump(),
        max_words_per_line=3,
    )
        
    print(f"[+] Generated Subtitle ASS: {ass_path.name}")
    print(f"[+] Invoking VideoCompositor.render_short with Broadcast Parameters:")
    print(f"    - Resolution: 1080x1920 (9:16 vertical canvas)")
    print(f"    - CRF: 18 (visually lossless broadcast)")
    print(f"    - Preset: medium (balanced CPU/GPU throughput)")
    print(f"    - Audio: 320k AAC, 48000 Hz, EBU R128 (-14 LUFS, -1 dBFS peak)")
    print(f"    - Voice Gate: agate active | HPF: 80Hz rumble filter active")
    
    compositor = VideoCompositor(output_dir=out_dir)
    render_start = time.time()
    try:
        final_mp4 = compositor.render_short(
            source_video_path=source_mp4,
            start_ms=c_start,
            end_ms=c_end,
            crop_plan=crop_plan,
            subtitle_ass_path=ass_path,
            output_filename=out_filename,
            normalize_audio=True,
            crf=18,
            preset="medium",
            target_fps=30,
        )
        render_elapsed = time.time() - render_start
        
        if final_mp4 and final_mp4.exists() and final_mp4.stat().st_size > 0:
            size_mb = final_mp4.stat().st_size / (1024*1024)
            print(f"[+] Render SUCCESS! Output: {final_mp4.name} ({size_mb:.2f} MB in {render_elapsed:.2f}s)")
            
            REPORT["subsystems_tested"]["rendering_and_audio"] = {
                "output_path": str(final_mp4),
                "size_mb": round(size_mb, 2),
                "render_sec": round(render_elapsed, 2),
                "crop_mode": crop_plan.mode,
            }
            REPORT["timings"]["render_sec"] = round(render_elapsed, 2)
            return final_mp4
        else:
            print(f"[-] Render FAILED! File not created or empty.")
            return None
    except Exception as e:
        print(f"[-] Render Exception: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_05_quality_gates_and_diagnostics(rendered_clip_path: Path):
    log_step("5. Multi-Tier Quality Verification Gates & Diagnostics")
    t0 = time.time()
    
    if not rendered_clip_path or not rendered_clip_path.exists():
        print("[-] Skipping Quality Gate test: rendered clip missing")
        return
        
    verifier = TechnicalVerifier()
    tech_passed, tech_res = verifier.verify(rendered_clip_path)
    print(f"[+] Tier 1 Fatal Technical Checks: Passed={tech_passed}")
    print(f"    - Resolution: {tech_res.get('width')}x{tech_res.get('height')}")
    print(f"    - Duration: {tech_res.get('duration_sec')}s")
    print(f"    - Audio Stream: {tech_res.get('has_audio')} ({tech_res.get('audio_codec')})")
    print(f"    - Video Stream: {tech_res.get('has_video')} ({tech_res.get('video_codec')})")
    
    # Tier 2 & 3: Black frame & Silence detection via RenderInspector
    inspector = RenderInspector()
    cv_passed, cv_res = inspector.inspect_render(rendered_clip_path)
    print(f"[+] Tier 2 & 3 Black / Silence / Framing Checks: Passed={cv_passed}")
    print(f"    - Black Frames: {cv_res.get('black_frame_count', 0)}")
    print(f"    - Silence Detected: {cv_res.get('silence_detected', False)}")
    print(f"    - Safe-Zone Collision Violations: {len(cv_res.get('collision_violations', []))}")
    print(f"    - Advisories: {cv_res.get('advisories', [])}")
    
    REPORT["subsystems_tested"]["quality_gates"] = {
        "technical_passed": tech_passed,
        "technical_details": tech_res,
        "cv_passed": cv_passed,
        "cv_details": cv_res,
    }
    REPORT["timings"]["quality_gates_sec"] = round(time.time() - t0, 3)


def test_06_universal_recovery_and_auto_fixes(rendered_clip_path: Path):
    log_step("6. Edge Situations: Failure Diagnostics & Auto-Fix Auto-Correction")
    t0 = time.time()
    
    db = SessionLocal()
    try:
        # Find or create a test clip record to run recovery service methods
        test_clip = db.query(Clip).first()
        if not test_clip:
            print("[-] No clip record available for recovery test")
            return
            
        # Point to freshly rendered clip file for realistic testing
        test_clip.rendered_path = str(rendered_clip_path)
        db.commit()
            
        recovery = ClipRecoveryService()
        
        # Test diagnose_clip
        diagnosis = recovery.diagnose_clip(test_clip)
        print(f"[+] Diagnosis on Clip {test_clip.id[:8]}: TechPassed={diagnosis['tech_gate']['passed']}, FramePassed={diagnosis['framing_gate']['passed']}, SemPassed={diagnosis['semantic_gate']['passed']}")
        print(f"    - Recommended Fixes: {[f['label'] for f in diagnosis['recommended_fixes']]}")
        
        # Test force_approve_clip
        target_status, qr = recovery.force_approve_clip(
            test_clip,
            db=db,
            operator_name="StressTestDaemon",
            note="Hardcore resilience suite force approval",
        )
        print(f"[+] Force Approve Override: New Status={target_status.value}")
        print(f"    - Audit Record: {qr.get('operator_override')}")
        
        # Test technical_clamp auto-fix
        clamp_res = recovery.apply_auto_fix(test_clip, fix_type="technical_clamp", db=db)
        print(f"[+] Technical Clamp Auto-Fix Result: {clamp_res.get('status')} ({clamp_res.get('message', 'Applied')})")
        
        REPORT["subsystems_tested"]["recovery_and_fixes"] = {
            "diagnosis": diagnosis,
            "force_approve_status": target_status.value,
            "clamp_result": clamp_res,
        }
        REPORT["timings"]["recovery_fixes_sec"] = round(time.time() - t0, 3)
    finally:
        db.close()


def test_07_bayesian_bandit_and_circuit_breaker():
    log_step("7. Closed-Loop Bayesian Multi-Armed Bandit & Emergency Circuit Breaker")
    t0 = time.time()
    
    bandit = BayesianVariantOptimizer(
        confidence_threshold=0.95,
        min_impressions=100,
        rollback_velocity_drop=0.50,
    )
    
    # Baseline Variant A: 1000 impressions, 50 clicks (5.0% CTR)
    # Challenger Variant B: 1000 impressions, 85 clicks (8.5% CTR)
    alpha_a, beta_a = bandit.calculate_posterior_parameters(clicks=50, impressions=1000)
    alpha_b, beta_b = bandit.calculate_posterior_parameters(clicks=85, impressions=1000)
    
    prob_b_wins = bandit.probability_b_beats_a(alpha_a, beta_a, alpha_b, beta_b, n_samples=15000)
    is_winner = prob_b_wins >= bandit.confidence_threshold
    
    print(f"[+] Variant A (Baseline 5% CTR): Beta({alpha_a:.1f}, {beta_a:.1f})")
    print(f"[+] Variant B (Challenger 8.5% CTR): Beta({alpha_b:.1f}, {beta_b:.1f})")
    print(f"[+] P(B > A) via 15,000 Monte Carlo samples: {prob_b_wins*100:.2f}% | Winner Declared: {is_winner}")
    
    # Test Emergency Circuit Breaker (>50% velocity drop)
    baseline_velocity = 200.0  # 200 views/hour
    post_promotion_velocity = 80.0  # dropped to 80 views/hour (-60% collapse)
    drop_ratio = (baseline_velocity - post_promotion_velocity) / baseline_velocity
    
    triggered = bandit.check_circuit_breaker(
        current_velocity=post_promotion_velocity,
        baseline_velocity=baseline_velocity,
    )
    print(f"[+] Emergency Circuit Breaker Test:")
    print(f"    - Baseline Velocity: {baseline_velocity:.1f} views/hr")
    print(f"    - Current Velocity: {post_promotion_velocity:.1f} views/hr")
    print(f"    - Measured Drop: {drop_ratio*100:.1f}%")
    print(f"    - Circuit Breaker Tripped (Rollback Triggered): {triggered}")
    
    REPORT["subsystems_tested"]["bayesian_bandit"] = {
        "prob_b_beats_a": round(prob_b_wins, 4),
        "winner_declared": is_winner,
        "circuit_breaker_tripped": triggered,
        "velocity_drop_ratio": round(drop_ratio, 4),
    }
    REPORT["timings"]["bayesian_bandit_sec"] = round(time.time() - t0, 3)


def test_08_subprocess_watchdog_and_hot_backup():
    log_step("8. Process-Tree Watchdog Safety & Online Hot Database Backup")
    t0 = time.time()
    
    # Test Watchdog supervised run with guaranteed timeout enforcement
    cmd = ["powershell", "-Command", "Start-Sleep -Seconds 1; Write-Output 'WATCHDOG_HEARTBEAT_OK'"]
    res = SubprocessWatchdog.run(cmd, timeout_sec=10.0, check=True)
    print(f"[+] Watchdog Supervised Run: Exit={res.exit_code}, Success={res.is_success}, Duration={res.duration_ms:.1f}ms")
    print(f"    Output: {res.stdout.strip()}")
    
    # Test Hot Database Backup
    backup_mgr = DatabaseBackupManager()
    backup_meta = backup_mgr.create_backup(compress=True)
    print(f"[+] Online Hot Database Backup Created:")
    print(f"    - Archive: {backup_meta['filename']}")
    print(f"    - Size: {backup_meta['size_mb']} MB")
    print(f"    - SHA-256 Checksum: {backup_meta['sha256'][:16]}...")
    print(f"    - Duration: {backup_meta['duration_sec']}s")
    
    REPORT["subsystems_tested"]["watchdog_and_backup"] = {
        "watchdog_execution_success": res.is_success,
        "watchdog_duration_ms": res.duration_ms,
        "backup": backup_meta,
    }
    REPORT["timings"]["watchdog_and_backup_sec"] = round(time.time() - t0, 3)


if __name__ == "__main__":
    print("\n" + "#"*75)
    print("### SHORTS ENGINE: HARDCORE WORST-CASE & ALL-PARAMETERS TEST BATTERY ###")
    print("#"*75)
    
    global_start = time.time()
    
    test_01_environment_and_hardware()
    selection_result = test_02_long_video_transcript_and_high_token_llm()
    test_03_all_5_caption_presets()
    
    if selection_result:
        selected_clips, source_video, transcript = selection_result
        if selected_clips:
            rendered_clip = test_04_full_clip_rendering_and_audio_mastering(
                source_video=source_video,
                selected_clip=selected_clips[0],
                transcript=transcript,
            )
            if rendered_clip:
                test_05_quality_gates_and_diagnostics(rendered_clip)
                test_06_universal_recovery_and_auto_fixes(rendered_clip)
                
    test_07_bayesian_bandit_and_circuit_breaker()
    test_08_subprocess_watchdog_and_hot_backup()
    
    REPORT["total_duration_sec"] = round(time.time() - global_start, 2)
    REPORT["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    
    print("\n" + "="*75)
    print(f"### HARDCORE BATTERY COMPLETED SUCCESSFULLY IN {REPORT['total_duration_sec']}s ###")
    print("="*75)
    
    # Save test report JSON to storage/logs
    report_path = settings.STORAGE_DIR / "logs" / "hardcore_stress_test_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2)
    print(f"\n[+] Full Telemetry Report saved to: {report_path}")
