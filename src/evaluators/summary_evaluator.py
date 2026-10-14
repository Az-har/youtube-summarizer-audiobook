import json
import logging
import re
from pathlib import Path

from src.models import EvaluationResult, Settings, Video

logger = logging.getLogger("SummaryEvaluator")

RE_NUMERICAL_ENTITY = re.compile(r'(?:\$|€|£|₹)?\b\d+(?:[.,]\d+)?%?\b')

WORD_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
    "hundred": "100", "thousand": "1000", "million": "1000000", "billion": "1000000000",
}


def audit_factual_numerical_grounding(source_text: str, draft_text: str) -> tuple[float, list[str]]:
    """
    Deterministic Factual Guard (Innovation 4):
    Pure-Python deterministic anchor running in <2ms. Extracts all numerical entities
    (currencies, percentages, quantities, metrics) from the summary script and validates
    that their underlying numeric values exist in the source transcript.
    Returns (deduction_penalty, list_of_issues).
    """
    if not draft_text or not source_text:
        return 0.0, []

    draft_matches = RE_NUMERICAL_ENTITY.findall(draft_text)
    if not draft_matches:
        return 0.0, []

    source_lower = source_text.lower()
    source_clean = re.sub(r'[,.]', '', source_lower)
    issues: list[str] = []

    for raw_match in set(draft_matches):
        cleaned = raw_match.strip("$€£₹%").replace(",", "")
        if not cleaned:
            continue

        # Check raw match, cleaned digits, or spoken number words
        found = False
        if raw_match.lower() in source_lower or cleaned in source_clean:
            found = True
        else:
            for word, digit in WORD_TO_DIGIT.items():
                if cleaned == digit and word in source_lower:
                    found = True
                    break

        if not found:
            issues.append(f"Deterministic Factual Guard: Unverified numerical claim '{raw_match}' not found in source transcript.")

    deduction = min(2.0, len(issues) * 0.4)
    return deduction, issues


def _call_critic_ollama(settings: Settings, prompt: str) -> dict:
    from src.processing import OllamaClient
    try:
        return OllamaClient.generate_json(settings.ollama_base_url, settings.ollama_model, prompt)
    except Exception as exc:
        logger.warning("Critic Ollama evaluation call failed: %s", exc)
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

    # Heuristic fast check for sponsor phrases and robotic seam clichés
    sponsor_triggers = [
        "like and subscribe", "hit the bell", "sponsor of this video",
        "use code", "link in description", "merch store", "patreon",
        "subscribe to my channel", "brought to you by",
    ]
    detected_sponsor_phrases = [p for p in sponsor_triggers if p in draft_script.lower()]

    cliche_triggers = [
        "in this section", "in this video", "the speaker discusses", "the speaker explains",
        "moving forward", "moving on", "as seen on screen", "look at the slide",
    ]
    detected_cliches = [c for c in cliche_triggers if c in draft_script.lower()]

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

    # Run Deterministic Factual Guard on numerical metrics (<2ms)
    guard_deduction, guard_issues = audit_factual_numerical_grounding(source_chunk_text, draft_script)

    if not res:
        # Fallback heuristic scoring if Ollama critic timed out
        score = 8.5
        issues = []
        if detected_sponsor_phrases:
            score -= 2.0 * len(detected_sponsor_phrases)
            issues.extend([f"Detected sponsor phrase: '{p}'" for p in detected_sponsor_phrases])
        if detected_cliches:
            score -= 1.0 * len(detected_cliches)
            issues.extend([f"Robotic framing or visual reference detected: '{c}'" for c in detected_cliches])
        if guard_issues:
            score -= guard_deduction
            issues.extend(guard_issues)
        status = "PASS" if score >= 8.0 else ("WARN" if score >= 6.0 else "FAIL")
        return EvaluationResult(
            stage="summarization",
            status=status,
            score=max(0.0, min(10.0, score)),
            issues=issues,
            metrics={"critic_mode": "heuristic_fallback", "guard_deduction": guard_deduction},
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

    if detected_cliches:
        for c in detected_cliches:
            if not any(c in issue.lower() for issue in issues):
                issues.append(f"Robotic framing or visual reference detected: '{c}'")
        flow = max(1.0, flow - len(detected_cliches) * 1.5)

    if guard_issues:
        issues.extend(guard_issues)
        faithfulness = max(1.0, faithfulness - (guard_deduction * 1.5))

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
            "factual_guard_deduction": guard_deduction,
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
    previous_section_tail: str = "",
) -> dict:
    """
    Self-Healing Agent: Re-generates and refines the draft script incorporating
    the specific Critic issues identified during valuation and enforcing narrative continuation.
    """
    from src.processing import OllamaClient

    issues_formatted = "\n".join(f"- {issue}" for issue in critique_issues)
    task = "near-complete cleaned read-aloud" if video.mode == "clean_readaloud" else "detailed information-first synthesis"

    continuation_directive = ""
    if previous_section_tail and previous_section_tail.strip():
        continuation_directive = f'''
NARRATIVE CONTINUATION REQUIREMENT:
The previous section concluded with: "{previous_section_tail.strip()}"
Ensure the refined draft weaves seamlessly from this thought without robotic transitional clichés (NEVER say "In this section", "Next", "Moving on").'''

    prompt = f'''You are refining a factual spoken audio digest section. The previous draft received the following critical feedback:

CRITIQUE ISSUES TO FIX:
{issues_formatted}
{continuation_directive}

INSTRUCTIONS:
1. Fix all issues mentioned in the critique above.
2. Completely remove any leftover sponsor reads, subscribe requests, promotional chatter, or robotic framing.
3. Ensure natural, fluent, spoken phrasing in {target_language} suitable for an audiobook.
4. Return ONLY valid JSON with this exact schema:
{{"script":"...","removed_segments":[{{"start":0,"end":0,"reason":"..."}}],"warnings":["..."]}}

Target Language: {target_language}
Mode: {task}

Original Speech Transcript:
{source_text}

Previous Flawed Draft:
{draft_script}'''

    return OllamaClient.generate_json(settings.ollama_base_url, settings.ollama_model, prompt)

