"""
Dynamic Smart Clip Limit Estimator & Length-Adaptive Sizing Service.
Calculates optimal number of viral Shorts to generate from a long-form video
based on total duration, usable speech ratio, target clip bounds, and pacing.
Also recommends the optimal AI model based on video duration and token footprint.
"""

from typing import Dict, Any, Tuple


def estimate_optimal_clip_count(
    video_duration_sec: float,
    target_duration_min: float = 30.0,
    target_duration_max: float = 60.0,
) -> Dict[str, Any]:
    """
    Computes mathematically sound, quality-focused clip count recommendations.

    Formula:
      1. Usable speech window excludes intro hook (5s) and outro wrap-up (10s).
      2. Average target clip duration = (target_min + target_max) / 2.
      3. Narrative pacing factor (1.35) accounts for ~35% transition, filler,
         or non-viral banter between distinct standalone segments.
      4. Dynamic bounds allow +/- flexibility for quality selection.
    """
    dur = max(0.0, float(video_duration_sec))
    t_min = max(10.0, float(target_duration_min))
    t_max = max(t_min + 5.0, float(target_duration_max))
    avg_clip_len = (t_min + t_max) / 2.0

    # Under 45 seconds: Cannot yield multiple distinct Shorts
    if dur < 45.0:
        clips_val = 1 if dur >= t_min else 0
        return {
            "duration_sec": round(dur, 1),
            "duration_display": f"{int(dur)}s",
            "recommended_clips": clips_val,
            "estimated_clips": clips_val,
            "recommended_range": f"{clips_val} clips",
            "min_clips": clips_val,
            "max_clips": clips_val,
            "usable_speech_sec": round(dur, 1),
            "pacing_factor": 1.0,
            "pacing": "balanced",
            "rationale": "Video is very short; at most 1 clip can be extracted without severe truncation.",
            "explanation": "Video is very short; at most 1 clip can be extracted without severe truncation.",
            "is_short_form": True,
            "suggested_model": "groq/llama-3.3-70b-versatile",
            "suggested_provider": "groq",
            "recommended_model": "groq",
            "model_name": "llama-3.3-70b-versatile",
        }

    # Usable window: deduct ~15s (intro & outro padding)
    usable_dur = max(10.0, dur - 15.0)

    # Narrative pacing: each high-virality hook requires context + payoff
    pacing_factor = 1.35
    raw_estimate = usable_dur / (avg_clip_len * pacing_factor)
    recommended = max(1, int(round(raw_estimate)))

    # Duration-based upper ceiling to prevent duplicate/diluted clips
    if dur <= 120.0:        # <= 2 minutes
        ceiling = 2
    elif dur <= 300.0:      # 2 - 5 minutes
        ceiling = 4
    elif dur <= 600.0:      # 5 - 10 minutes
        ceiling = 7
    elif dur <= 1200.0:     # 10 - 20 minutes
        ceiling = 12
    elif dur <= 2400.0:     # 20 - 40 minutes
        ceiling = 18
    else:                   # > 40 minutes / 1-2 hours
        ceiling = 25

    recommended = min(recommended, ceiling)
    min_clips = max(1, recommended - 1)
    max_clips = min(ceiling, max(recommended, recommended + 2))

    # Duration display string
    mins = int(dur // 60)
    secs = int(dur % 60)
    dur_display = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

    # AI Model Recommendation based on duration
    prov, model, model_reason = select_optimal_model_for_duration(dur)

    rationale = (
        f"For a {dur_display} video with {int(avg_clip_len)}s target clips, "
        f"approximately {recommended} high-engagement segments exist (bounds: {min_clips}–{max_clips}) "
        f"without repetitive overlap or context dilution."
    )

    return {
        "duration_sec": round(dur, 1),
        "duration_display": dur_display,
        "recommended_clips": recommended,
        "estimated_clips": recommended,
        "recommended_range": f"{min_clips}-{max_clips} clips",
        "min_clips": min_clips,
        "max_clips": max_clips,
        "usable_speech_sec": round(usable_dur, 1),
        "pacing_factor": pacing_factor,
        "pacing": "balanced",
        "rationale": rationale,
        "explanation": rationale,
        "is_short_form": False,
        "suggested_provider": prov,
        "suggested_model": model,
        "recommended_model": prov,
        "model_name": model,
        "model_selection_reason": model_reason,
    }


def select_optimal_model_for_duration(video_duration_sec: float) -> Tuple[str, str, str]:
    """
    Selects the optimal AI provider and model based on video duration:
    - Short (< 5 min): Groq LPU (llama-3.3-70b-versatile) for sub-second low-latency inference.
    - Medium (5 - 20 min): Gemini 2.0 Flash for massive throughput and balanced reasoning.
    - Long (> 20 min / 1-2 hours): Gemini 1.5 Pro / Flash with 1M-2M context window for global narrative arc.
    """
    dur = float(video_duration_sec)
    if dur < 300.0:
        return (
            "groq",
            "llama-3.3-70b-versatile",
            "Video is under 5 minutes; Groq LPU delivers sub-second inference with sharp hook detection.",
        )
    elif dur <= 1200.0:
        return (
            "gemini",
            "gemini-2.0-flash",
            "Video is 5–20 minutes; Gemini 2.0 Flash provides balanced reasoning across extended context.",
        )
    else:
        return (
            "gemini",
            "gemini-1.5-pro",
            "Long-form video (>20 minutes); Gemini 1.5 Pro provides 1M+ token context to grasp overarching narrative arcs.",
        )
