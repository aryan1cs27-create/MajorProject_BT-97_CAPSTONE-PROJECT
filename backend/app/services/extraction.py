"""Dynamic skill extraction.

Two complementary extractors, both pure functions of the source text:

1. **Dictionary-assisted** — scans the text for taxonomy names/aliases with
   token-boundary, case-insensitive matching, alias normalisation and an extra
   contextual check for ambiguous terms (``Go``, ``R``, ``IR`` …).
2. **Generic phrase fallback** — proposes candidate skills from contexts where
   skills normally appear (comma/slash lists, cue-preposition phrases such as
   "experience with X", "X developer", technical spellings like ``C++`` or
   ``Node.js``). Candidates that pass the plausibility gate are returned as
   *discovered* skills with a confidence value and their source phrase kept
   verbatim — they can be scored before anyone adds them to the taxonomy.

Nothing here requires an LLM or a paid API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from app.services.taxonomy import Taxonomy, get_taxonomy

# Context words that make an otherwise ambiguous token credible as a skill.
CUE_WORDS = (
    "skill", "skills", "experienced", "experience", "knowledge", "proficiency",
    "proficient", "expertise", "developer", "engineering", "engineer", "analyst",
    "administrator", "architect", "scientist", "programming", "framework",
    "platform", "tool", "tools", "tooling", "certified", "certification",
    "degree", "diploma", "qualification", "qualifications", "responsibilities",
    "familiarity", "hands-on", "technologies", "stack", "backend", "frontend",
    "devops", "expert", "understanding", "hiring", "looking", "required",
    "preferred",
)
_CUE_RE = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in CUE_WORDS) + r")\b", re.IGNORECASE)

# Role/family words used both for skill phrases and title detection.
ROLE_WORDS = (
    "developer", "engineer", "analyst", "architect", "administrator", "admin",
    "scientist", "designer", "consultant", "specialist", "technician",
    "administrator", "manager", "lead", "intern", "trainer", "engineer",
    "sre", "devops", "programmer", "tester", "support",
)
ROLE_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in ROLE_WORDS) + r")\b", re.I)

EDUCATION_RE = re.compile(
    r"\b(ph\.?d|doctorate|master'?s?|m\.?s\.?c|mtech|bachelor'?s?|b\.?s\.?c|btech|"
    r"mba|diploma|degree)\b",
    re.IGNORECASE,
)

# Prepositions that introduce a skill phrase: "experience with Kubernetes"
CUE_PREP_RE = re.compile(
    r"\b(?:experience|knowledge|familiarity|exposure|expertise|proficiency|"
    r"understanding|worked|working|using|skilled)\s+(?:in|with|on|of|as)\s+"
    r"([A-Za-z0-9][A-Za-z0-9+#. &/-]{1,40})",
    re.IGNORECASE,
)

# "React developer", "cloud security engineer", "data analyst"
CUE_ROLE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9+#.]{1,24}(?:\s+[A-Za-z][A-Za-z0-9+#.]{0,24}){0,2})\s+"
    r"(?:" + "|".join(re.escape(w) for w in ROLE_WORDS) + r")\b",
    re.IGNORECASE,
)

# "built internal tooling with Kustomize" — a verb phrase introducing a tool.
CUE_WITH_RE = re.compile(
    r"\b(?:with|using|via|leveraging)\s+([A-Za-z0-9][A-Za-z0-9+#. &/-]{1,40})",
    re.IGNORECASE,
)

# Tokens whose spelling itself signals technology: C++, C#, .NET, Node.js, Next.js
TECH_SPELLING_RE = re.compile(r"^(?:[A-Za-z0-9]+\.[A-Za-z]+|[A-Za-z]\+\+|[A-Za-z]#|\.NET)$")

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "for", "with", "on", "at",
    "by", "as", "is", "are", "be", "we", "our", "my", "i", "you", "your", "this",
    "that", "these", "those", "it", "its", "from", "into", "via", "per", "all",
    "any", "new", "more", "other", "such", "each", "their", "team", "teams",
    "work", "working", "role", "roles", "job", "position", "candidate",
    "applicants", "apply", "years", "year", "strong", "good", "great", "well",
    "ability", "abilities", "including", "include", "etc", "across", "within",
}

LIST_SPLIT_RE = re.compile(r"\s*(?:[,;|]|\s/\s|\n)\s*")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# "Skills:" / "Tech stack:" style labels in front of a list item
LABEL_PREFIX_RE = re.compile(
    r"^(?:skills?|technolog(?:y|ies)|tech\s*stack|stack|tools|technologies\s+used|"
    r"core\s+competencies|competencies|keywords|areas?\s+of\s+expertise|"
    r"responsibilities|duties|requirements|qualifications|preferred|nice\s+to\s+have|"
    r"must\s+have|required|about\s+the\s+role|the\s+role|about\s+us)\s*[:\-–]\s*",
    re.IGNORECASE,
)

# Generic nouns that are almost never a skill on their own. Used only to stop
# the discovery fallback from inventing skills like "infrastructure".
GENERIC_NOUNS = {
    "infrastructure", "environment", "environments", "solution", "solutions",
    "architecture", "architectures", "process", "processes", "business",
    "customer", "customers", "user", "users", "project", "projects",
    "requirement", "requirements", "deliverable", "deliverables", "goal",
    "goals", "performance", "technology", "technologies", "technique",
    "techniques", "concept", "concepts", "methodology", "methodologies",
    "system", "systems", "tool", "tools", "platform", "platforms",
    "application", "applications", "service", "services", "software",
    "hardware", "development", "engineering", "management", "operations",
    "opportunity", "opportunities", "position", "team", "teams", "skill",
    "skills", "knowledge", "understanding", "background", "level", "quality",
    "standard", "standards", "practice", "practices", "responsibility",
    "responsibilities", "accountability", "stakeholder", "stakeholders",
    "value", "values", "need", "needs", "way", "ways", "area", "areas",
    "field", "fields", "domain", "domains", "sector", "sectors", "industry",
}

# Education / affiliation words: these are qualifications, not skills.
EDUCATION_CONTEXT_RE = re.compile(
    r"\b(?:b\.?s\.?c|btech|m\.?s\.?c|mba|ph\.?d|bachelor|master|degree|diploma|"
    r"university|college|institute|school|graduated|honours?)\b",
    re.IGNORECASE,
)

# Action verbs at the start of a clause describe responsibilities, not skills.
ACTION_VERBS = {
    "led", "built", "build", "developed", "develop", "created", "create",
    "managed", "manage", "designed", "design", "implemented", "implement",
    "handled", "handle", "wrote", "write", "tested", "test", "deployed",
    "deploy", "mentored", "mentor", "ran", "run", "made", "make", "took",
    "own", "owned", "deliver", "delivered", "improved", "reduced", "increased",
    "responsible", "ensured", "ensure", "supported", "support", "assisted",
    "participated", "collaborated", "worked", "prepare", "prepared", "monitor",
    "monitored", "maintained", "maintain", "migrate", "migrated", "automated",
    "automate", "launched", "launch", "grew", "grow", "drove", "drive",
}

# Common tech suffixes that make a two-word capitalised phrase look like a
# technology rather than a person's name ("Snowflake DB", "React Native").
TECH_SUFFIXES = {
    "db", "api", "sdk", "ml", "ai", "cloud", "js", "ts", "io", "kit",
    "stack", "engine", "framework", "lib", "library", "server", "client",
    "cli", "ui", "ux", "etl", "ops", "ware", "base", "lab", "os",
    "native", "core", "plus", "script", "lang", "query", "learn",
}

MAX_PHRASE_CHARS = 48
MAX_PHRASE_WORDS = 5


@dataclass(frozen=True)
class ExtractedSkill:
    """One piece of skill evidence found in a document."""

    phrase: str                      # exactly as found in the source
    canonical: str | None = None     # taxonomy canonical when normalised
    category: str | None = None
    confidence: float = 0.5
    source: str = "taxonomy"         # "taxonomy" | "discovered"
    evidence: str = ""               # source snippet
    match_type: str = "alias"        # alias | exact | phrase | cue | list | spelling

    @property
    def key(self) -> str:
        """Stable, case-insensitive key used for set comparisons."""
        return (self.canonical or self.phrase).lower()

    @property
    def label(self) -> str:
        return self.canonical or self.phrase


def _snippet(text: str, start: int, end: int, width: int = 60) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    snippet = " ".join(text[left:right].split())
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def _is_list_context(text: str, start: int, end: int) -> bool:
    """True when the match is a comma/semicolon list element (skills lists)."""
    left = text[:start].rstrip(" \t")
    right = text[end:].lstrip(" \t")
    left_ok = not left or left[-1] in ",;|/(\n-–—:"
    right_ok = not right or right[0] in ",;|)/\n-–—:"
    return left_ok and right_ok


def _line_of(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return text[start:end]


def extract_taxonomy_skills(text: str, taxonomy: Taxonomy | None = None) -> list[ExtractedSkill]:
    """Dictionary-assisted pass: find known skills/aliases in ``text``."""
    if not text:
        return []
    taxonomy = taxonomy or get_taxonomy()
    found: dict[str, ExtractedSkill] = {}

    # Longest names first so "Spring Boot" wins over "Spring".
    names = sorted(taxonomy.aliases, key=len, reverse=True)
    consumed: list[tuple[int, int]] = []
    for name in names:
        pattern = re.compile(
            r"(?<![A-Za-z0-9_+#])" + re.escape(name) + r"(?![A-Za-z0-9_+#])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            # skip matches inside a longer skill already recognised ("React" in
            # "React Native", "Spring" in "Spring Boot")
            if any(start < match.end() and match.start() < end for start, end in consumed):
                continue
            entry = taxonomy.entry_for_name(name)
            if entry is None:
                continue
            matched_text = match.group(0)
            ambiguous = entry.ambiguous or len(matched_text) <= 1
            if ambiguous:
                # "go" in "go to market" is not Go, nor is "R" in "her R for".
                # Accept only with a skills cue on the line, or when the token
                # sits in a list ("R, Python, pandas").
                line = _line_of(text, match.start())
                in_list = _is_list_context(text, match.start(), match.end())
                if not in_list and not _CUE_RE.search(line):
                    continue
            key = entry.canonical.lower()
            if key in found:
                continue
            consumed.append((match.start(), match.end()))
            is_exact = matched_text.lower() == entry.canonical.lower()
            found[key] = ExtractedSkill(
                phrase=matched_text,
                canonical=entry.canonical,
                category=entry.category,
                confidence=0.95,
                source="taxonomy",
                evidence=_snippet(text, match.start(), match.end()),
                match_type="exact" if is_exact else "alias",
            )
    return list(found.values())


def _looks_like_name(words: list[str]) -> bool:
    """Consecutive capitalised words with no tech marker read as a person name
    ("Alice Johnson") or as a job title ("Senior Backend Engineer")."""
    if len(words) < 2:
        return False
    capitalised = [word for word in words if word[:1].isupper()]
    if len(capitalised) < 2:
        return False
    # a tech suffix/spelling rescues the phrase ("React Native", "Snowflake DB")
    if any(TECH_SPELLING_RE.match(word) for word in words):
        return False
    if words[-1].lower() in TECH_SUFFIXES or words[0].lower() in TECH_SUFFIXES:
        return False
    if ROLE_RE.search(words[-1]):
        return True          # title-like, handled by the title extractor instead
    return True


def _candidate_plausibility(phrase: str, context: str, *, in_list: bool) -> float | None:
    """Return a confidence 0..1, or None when the phrase should be rejected."""
    phrase = " ".join(phrase.split()).strip(" -–—:.")
    phrase = LABEL_PREFIX_RE.sub("", phrase).strip()
    if not phrase or len(phrase) > MAX_PHRASE_CHARS:
        return None
    words = phrase.split()
    if len(words) > MAX_PHRASE_WORDS:
        return None
    if all(word.lower() in STOPWORDS for word in words):
        return None
    if words[0].lower() in ACTION_VERBS:
        return None                      # responsibilities are not skills
    if len(words) == 1 and words[0].lower() in GENERIC_NOUNS:
        return None                      # "infrastructure" is not a skill claim
    if EDUCATION_CONTEXT_RE.search(phrase):
        return None                      # qualifications are tracked separately
    # Reject full clauses: too many stopword/function words.
    stop_ratio = sum(1 for w in words if w.lower() in STOPWORDS) / len(words)
    if stop_ratio > 0.4:
        return None
    if _looks_like_name(words):
        return None

    technical_spelling = any(TECH_SPELLING_RE.match(word) for word in words)
    has_cue_context = bool(_CUE_RE.search(context))
    ends_with_role = bool(ROLE_RE.search(words[-1]))
    has_special = bool(re.search(r"[+#.0-9]", phrase))
    starts_upper = phrase[:1].isupper()

    confidence = 0.4
    if in_list:
        confidence += 0.2
    if ends_with_role:
        confidence += 0.15
    if technical_spelling:
        confidence += 0.2
    if has_special:
        confidence += 0.05
    if has_cue_context:
        confidence += 0.1
    if starts_upper:
        confidence += 0.05

    # A bare capitalised noun with no supporting context is too weak.
    if not (in_list or ends_with_role or technical_spelling or has_cue_context):
        return None
    return round(min(confidence, 0.95), 2)


def extract_discovered_skills(
    text: str,
    known_keys: set[str],
    taxonomy: Taxonomy | None = None,
) -> list[ExtractedSkill]:
    """Generic fallback: propose skills that are *not* in the taxonomy.

    ``known_keys`` holds keys already produced by the dictionary pass so the
    fallback does not rediscover them.
    """
    if not text:
        return []
    taxonomy = taxonomy or get_taxonomy()
    candidates: dict[str, ExtractedSkill] = {}

    def add(phrase: str, context: str, *, in_list: bool, match_type: str) -> None:
        phrase = " ".join(phrase.split()).strip(" -–—:.")
        phrase = LABEL_PREFIX_RE.sub("", phrase).strip(" -–—:.:")
        if not phrase:
            return
        # "A and B" is a compound phrase — score each part on its own.
        if re.search(r"\s(?:and|or|&)\s", phrase, re.IGNORECASE):
            for part in re.split(r"\s(?:and|or|&)\s", phrase, flags=re.IGNORECASE):
                if part.strip():
                    add(part, context, in_list=in_list, match_type=match_type)
            return
        key = phrase.lower()
        if key in known_keys or key in candidates:
            return
        if taxonomy.normalize(phrase):          # already a taxonomy skill
            return
        if key in STOPWORDS:
            return
        confidence = _candidate_plausibility(phrase, context, in_list=in_list)
        if confidence is None:
            return
        start = context.find(phrase)
        candidates[key] = ExtractedSkill(
            phrase=phrase,
            canonical=None,
            category=None,
            confidence=confidence,
            source="discovered",
            evidence=_snippet(context, max(start, 0), max(start, 0) + len(phrase)),
            match_type=match_type,
        )

    # 1) list contexts (comma / semicolon / pipe / slash separated)
    has_delimiter = bool(re.search(r"[,;|]|\s/\s|\n", text))
    for raw_segment in LIST_SPLIT_RE.split(text):
        segment = raw_segment.strip()
        if not segment or len(segment) > MAX_PHRASE_CHARS:
            continue
        index = text.find(raw_segment)
        line = _line_of(text, index) if index >= 0 else text
        add(segment, line, in_list=has_delimiter, match_type="list")

    # 2) "experience with X", "skilled in X" — the tail may hold several items
    for match in CUE_PREP_RE.finditer(text):
        tail = match.group(1)
        pieces = re.split(r"\s*(?:,|;|\band\b|\bfor\b|\bplus\b|\bwho\b|\bwhile\b)\s*", tail)
        for piece in pieces:
            add(piece, _line_of(text, match.start()), in_list=False, match_type="cue")

    # 2b) "built tooling with X" — only when the line carries a skills cue
    for match in CUE_WITH_RE.finditer(text):
        line = _line_of(text, match.start())
        if not _CUE_RE.search(line):
            continue
        tail = match.group(1)
        pieces = re.split(r"\s*(?:,|;|\band\b|\bfor\b|\bplus\b)\s*", tail)
        for piece in pieces:
            add(piece, line, in_list=False, match_type="cue")

    # 3) "X developer / X engineer"
    for match in CUE_ROLE_RE.finditer(text):
        add(match.group(0), _line_of(text, match.start()), in_list=False, match_type="cue")

    # 4) technical spellings anywhere (C++, C#, .NET, Node.js …)
    for match in re.finditer(r"[A-Za-z0-9+#.]{2,20}", text):
        token = match.group(0)
        if TECH_SPELLING_RE.match(token):
            add(token, text, in_list=False, match_type="spelling")

    return list(candidates.values())


def extract_skills(
    text: str,
    taxonomy: Taxonomy | None = None,
    *,
    include_discovered: bool = True,
) -> list[ExtractedSkill]:
    """Full pipeline: taxonomy pass, then generic discovery, deduplicated."""
    taxonomy = taxonomy or get_taxonomy()
    skills = extract_taxonomy_skills(text, taxonomy)
    if include_discovered:
        known = {skill.key for skill in skills}
        skills.extend(extract_discovered_skills(text, known, taxonomy))
    # dedupe by key, keeping the highest confidence / taxonomy entries first
    deduped: dict[str, ExtractedSkill] = {}
    for skill in skills:
        current = deduped.get(skill.key)
        if current is None or (skill.source, skill.confidence) > (current.source, current.confidence):
            deduped[skill.key] = skill
    return sorted(deduped.values(), key=lambda s: (-s.confidence, s.label.lower()))


def normalize_phrase(phrase: str, taxonomy: Taxonomy | None = None) -> str:
    """Canonical name when known, otherwise the trimmed original phrase."""
    taxonomy = taxonomy or get_taxonomy()
    canonical = taxonomy.normalize(phrase)
    if canonical:
        return canonical
    return " ".join((phrase or "").split())
