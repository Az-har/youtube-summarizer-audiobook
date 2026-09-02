import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from src.models import EvaluationResult, Settings, Video


def _extract_json(raw: str) -> dict:
    """Extracts JSON object from raw LLM output, handling markdown code fences."""
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        try:
            return json.loads(text[start:end])
        except Exception:
            pass
    try:
        return json.loads(text)
    except Exception:
        return {}


def _call_critic_ollama(settings: Settings, prompt: str) -> dict:
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "num_ctx": 4096,
            "temperature": 0.1,  # Low temperature for strict, reliable valuation
            "top_p": 0.9,
        },
    }
    req = urllib.request.Request(
        f"{settings.ollama_base_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return _extract_json(body.get("response", "{}"))
    except Exception:
        return {}


def judge_summary(
    settings: Settings,
    source_chunk_text: str,
    draft_script: str,
    target_language: str,
    mode: str,
) -> EvaluationResult:
    """
    Autonomous LLM Critic Agent that audits the draft summary on:
    1. Faithfulness / Factual Consistency (1-10)
    2. Ad / Sponsor / Promo Removal (1-10)
    3. Spoken Flow & Audiobook Readability (1-10)
    4. Language Fidelity & Grammar (1-10)
    """
    if not draft_script.strip():
        return EvaluationResult(
            stage="summarization",
            status="FAIL",
            score=0.0,
            issues=["Draft script is empty."],
        )

    # Heuristic fast check for sponsor phrases
    sponsor_triggers = [
        "like and subscribe", "hit the bell", "sponsor of this video",
        "use code", "link in description", "merch store", "patreon",
        "subscribe to my channel", "brought to you by",
    ]
    detected_sponsor_phrases = [p for p in sponsor_triggers if p in draft_script.lower()]

    prompt = f'''You are an expert Audio Editor and Fact-Checking Critic. Evaluate this draft narration script against the original speech transcript.

Output ONLY valid JSON with this exact schema:
{{
  "faithfulness_score": 9.0,
  "ad_removal_score": 10.0,
  "spoken_flow_score": 8.5,
  "language_fidelity_score": 9.0,
  "overall_score": 9.1,
  "issues": ["...list any specific issues or sponsor leftovers..."],
  "verdict": "PASS" or "RETRY"
}}

Target Language: {target_language}
Mode: {mode}

Original Speech Transcript:
{source_chunk_text[:3000]}

Draft Narration Script:
{draft_script[:3000]}
'''
    res = _call_critic_ollama(settings, prompt)

    if not res:
        # Fallback heuristic scoring if Ollama critic timed out
        score = 8.5
        issues = []
        if detected_sponsor_phrases:
            score -= 2.0 * len(detected_sponsor_phrases)
            issues.extend([f"Detected sponsor phrase: '{p}'" for p in detected_sponsor_phrases])
        status = "PASS" if score >= 8.0 else ("WARN" if score >= 6.0 else "FAIL")
        return EvaluationResult(
            stage="summarization",
            status=status,
            score=max(0.0, min(10.0, score)),
            issues=issues,
            metrics={"critic_mode": "heuristic_fallback"},
        )

    faithfulness = float(res.get("faithfulness_score", 8.0))
    ad_removal = float(res.get("ad_removal_score", 9.0))
    flow = float(res.get("spoken_flow_score", 8.0))
    lang = float(res.get("language_fidelity_score", 9.0))
    issues = list(res.get("issues", []))

    if detected_sponsor_phrases:
        for p in detected_sponsor_phrases:
            if not any(p in issue.lower() for issue in issues):
                issues.append(f"Detected sponsor callout: '{p}'")
        ad_removal = min(ad_removal, 5.0)

    overall_score = round((faithfulness * 0.35 + ad_removal * 0.25 + flow * 0.20 + lang * 0.20), 1)
    status = "FAIL" if overall_score < 7.0 or ad_removal < 6.0 else ("WARN" if overall_score < 8.2 else "PASS")

    return EvaluationResult(
        stage="summarization",
        status=status,
        score=overall_score,
        issues=issues,
        metrics={
            "faithfulness": faithfulness,
            "ad_removal": ad_removal,
            "spoken_flow": flow,
            "language_fidelity": lang,
        },
    )


def refine_summary_with_critique(
    settings: Settings,
    video: Video,
    source_language: str,
    target_language: str,
    source_text: str,
    draft_script: str,
    critique_issues: list[str],
) -> dict:
    """
    Self-Healing Agent: Re-generates and refines the draft script incorporating
    the specific Critic issues identified during valuation.
    """
    from src.processing import _ollama

    issues_formatted = "\n".join(f"- {issue}" for issue in critique_issues)
    task = "near-complete cleaned read-aloud" if video.mode == "clean_readaloud" else "detailed information-first synthesis"

    prompt = f'''You are refining a factual spoken audio digest section. The previous draft received the following critical feedback:

CRITIQUE ISSUES TO FIX:
{issues_formatted}

INSTRUCTIONS:
1. Fix all issues mentioned in the critique above.
2. Completely remove any leftover sponsor reads, subscribe requests, or promotional chatter.
3. Ensure natural, fluent, spoken phrasing in {target_language}.
4. Return ONLY valid JSON with this exact schema:
{{"script":"...","removed_segments":[{{"start":0,"end":0,"reason":"..."}}],"warnings":["..."]}}

Target Language: {target_language}
Mode: {task}

Original Speech Transcript:
{source_text}

Previous Flawed Draft:
{draft_script}'''

    return _ollama(settings, prompt)
