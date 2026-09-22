"""
Prompts and schemas for AI Clip Selection (Generator + Critic passes).
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class TitleHookVariation(BaseModel):
    title: str = Field(description="High-CTR title variant under 60 characters")
    angle: str = Field(description="Psychological angle: 'Curiosity Gap', 'High Stakes', or 'Contrarian'")
    hook_overlay: Optional[str] = Field(default=None, description="Short punchy 3-5 word text for on-screen hook overlay during first 3 seconds")


class CandidateSegmentSchema(BaseModel):
    start_sec: float = Field(description="Approximate start time in seconds")
    end_sec: float = Field(description="Approximate end time in seconds covering the entire concluding sentence")
    hook_text: str = Field(description="Exact verbatim spoken dialogue of the opening hook within the first 1-2 sentences. Do NOT summarize or paraphrase.")
    payoff_text: str = Field(description="Exact verbatim spoken dialogue of the concluding punchline, takeaway, or resolution in the final 1-2 sentences. Do NOT summarize or paraphrase.")
    score: float = Field(ge=0.0, le=1.0, description="Quality and viral potential score (0.0 to 1.0)")
    reason: str = Field(description="Detailed editorial justification for why this segment works as a standalone Short")
    suggested_title: str = Field(description="High-CTR YouTube Shorts title (under 60 characters)")
    suggested_description: str = Field(description="Short description with 3-5 relevant hashtags")
    suggested_tags: List[str] = Field(description="List of 3-7 tags (e.g. ['#shorts', '#tech'])")
    variations: Optional[List[TitleHookVariation]] = Field(
        default=None,
        description="3 distinct A/B title and hook variations exploring different psychological angles"
    )
    broll_keywords: Optional[List[str]] = Field(
        default=None,
        description="2-3 visual search keywords for relevant stock B-roll cutaway footage (e.g. ['coding keyboard', 'data matrix'])"
    )



class CandidateSegmentsResponse(BaseModel):
    candidates: List[CandidateSegmentSchema] = Field(
        description="Ranked list of standalone Short candidates extracted from the transcript"
    )


GENERATOR_CRITIC_PROMPT = """You are an elite short-form video editor and YouTube algorithm strategist for top creators.
Your task is to analyze a timestamped transcript of a long-form video and extract the top {min_count} to {max_count} most compelling, high-retention vertical Short clips.

EVALUATION RUBRIC:
1. HOOK STRENGTH (0-3s): Does the clip immediately capture attention with a provocative question, shocking claim, counter-intuitive insight, or high curiosity gap? The segment MUST begin on the very first word of a complete grammatical sentence. Set hook_text to the EXACT verbatim spoken words.
2. STANDALONE AUTONOMY: Can a viewer understand the central point without confusion? Avoid clips that rely on missing visuals or unexplained personal references.
3. PAYOFF & RESOLUTION: Does the clip deliver on the premise established by the hook? Does it provide a clean punchline, conclusion, or takeaway? The segment MUST end on the final punctuation mark of a complete thought or punchline. Set payoff_text to the EXACT verbatim spoken concluding words.
4. DURATION FIT: Target between {min_dur}s and {max_dur}s. Ensure end_sec covers the ENTIRE spoken payoff sentence so the punchline is NEVER cut off mid-thought or mid-sentence.

TRANSCRIPT WITH TIMESTAMPS:
{transcript_text}

Extract {min_count} to {max_count} candidate clips strictly conforming to the requested JSON schema. Rank them by overall quality score.
"""
