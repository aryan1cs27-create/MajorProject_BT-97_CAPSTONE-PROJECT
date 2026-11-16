"""Feature extraction: turn a resume row and a job row into structured profiles.

Rules
-----
* Only information actually present in the source is used. Anything not found
  is reported as ``None`` ("unknown") — never as zero or as an assumption.
* The same extractor runs over both documents, so one engine works for every
  technology domain; nothing is job-category specific.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.job import Job
from app.models.resume import Resume
from app.services.extraction import EDUCATION_RE, ROLE_RE, ExtractedSkill, extract_skills, normalize_phrase
from app.services.taxonomy import get_taxonomy

# ---------------------------------------------------------------- experience
_EXPERIENCE_PATTERNS = [
    # "5+ years of experience", "5 years experience"
    re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to\s+)?\s*(?:years?|yrs?)\b[^.;\n]{0,30}?\bexperience", re.I),
    re.compile(r"(?:experience|worked)\s+(?:of\s+|for\s+|as\s+)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I),
    re.compile(r"over\s+(\d{1,2})\s*(?:\+\s*)?years?", re.I),
    re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*years?\s*(?:of\s*)?experience", re.I),
]

SENIORITY_MARKERS = [
    ("intern", re.compile(r"\b(intern|internship|trainee|graduate|junior|entry[- ]level)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate)\b", re.I)),
    ("mid", re.compile(r"\b(mid[- ]level|intermediate)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|advanced)\b", re.I)),
    ("lead", re.compile(r"\b(lead|tech lead|team lead)\b", re.I)),
    ("principal", re.compile(r"\b(principal|staff|head of|director|chief)\b", re.I)),
]


@dataclass
class ResumeProfile:
    resume_id: int
    candidate_name: str | None
    skills: list[ExtractedSkill] = field(default_factory=list)
    skill_keys: set[str] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)
    certifications: list[str] = field(default_factory=list)
    years_experience: float | None = None      # None = not stated, NOT zero
    titles: list[str] = field(default_factory=list)
    education: str | None = None
    seniority_hint: str | None = None
    text_length: int = 0

    @property
    def skill_labels(self) -> dict[str, str]:
        """key -> display label."""
        return {skill.key: skill.label for skill in self.skills}


@dataclass
class JobProfile:
    job_id: int
    title: str
    company: str
    location: str | None
    employment_type: str | None
    role_family: str | None
    seniority: str | None
    minimum_experience: float | None
    description: str
    # Normalised requirement keys (canonical name when known, else raw phrase)
    required_keys: list[str] = field(default_factory=list)
    preferred_keys: list[str] = field(default_factory=list)
    required_labels: dict[str, str] = field(default_factory=dict)
    preferred_labels: dict[str, str] = field(default_factory=dict)
    resume_skill_keys: set[str] = field(default_factory=set)  # set later
    description_skills: list[ExtractedSkill] = field(default_factory=list)
    required_certifications: list[str] = field(default_factory=list)
    education_mentioned: bool = False


# ------------------------------------------------------------------ resumes
def _extract_years(text: str) -> float | None:
    for pattern in _EXPERIENCE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        if len(groups) == 2:
            try:
                return float(max(int(groups[0]), int(groups[1])))
            except ValueError:
                continue
        if groups:
            try:
                return float(groups[0])
            except ValueError:
                continue
    return None


def _extract_titles(text: str, candidate_name: str | None = None) -> list[str]:
    titles: list[str] = []
    name = (candidate_name or "").strip()
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip(" ·|-–—\t")
        if name and line.lower().startswith(name.lower()):
            line = line[len(name):].strip(" -,–—") or line
        if not line or len(line) > 70:
            continue
        if ROLE_RE.search(line) and not line.endswith("."):
            # keep short lines that read like a role, avoid full sentences
            if len(line.split()) <= 8 and line[:1].isupper():
                if line not in titles:
                    titles.append(line)
        if len(titles) >= 8:
            break
    return titles


def _extract_education(text: str) -> str | None:
    match = EDUCATION_RE.search(text)
    if not match:
        return None
    level = match.group(1).lower()
    if level.startswith(("ph", "doctorate")):
        return "doctorate"
    if level.startswith(("master", "m.", "mba", "mtech")):
        return "master"
    if level.startswith(("bachelor", "b.", "btech")):
        return "bachelor"
    if level.startswith("diploma"):
        return "diploma"
    return level


def _seniority_hint(titles: list[str], text: str) -> str | None:
    haystack = " ".join(titles[:3]) or text[:400]
    for label, pattern in SENIORITY_MARKERS:
        if pattern.search(haystack):
            return label
    return None


def build_resume_profile(resume: Resume) -> ResumeProfile:
    text = resume.extracted_text or ""
    skills = extract_skills(text)
    taxonomy = get_taxonomy()

    categories = {
        taxonomy.category_of(skill.canonical)
        for skill in skills
        if skill.canonical and taxonomy.category_of(skill.canonical)
    }
    certifications = [
        skill.label for skill in skills
        if taxonomy.category_of(skill.canonical or "") == "certification"
    ]
    titles = _extract_titles(text, resume.candidate_name)

    return ResumeProfile(
        resume_id=resume.id,
        candidate_name=resume.candidate_name,
        skills=skills,
        skill_keys={skill.key for skill in skills},
        categories={c for c in categories if c},
        certifications=certifications,
        years_experience=_extract_years(text),
        titles=titles,
        education=_extract_education(text),
        seniority_hint=_seniority_hint(titles, text),
        text_length=len(text),
    )


# -------------------------------------------------------------------- jobs
def _role_family(title: str, description: str) -> str | None:
    """Coarse family used only for reporting, never for branching the algorithm."""
    haystack = f"{title} {description[:300]}".lower()
    families = [
        ("security", r"secur|soc|pentest|appsec|vulnerab|incident"),
        ("data", r"\bdata\b|analytics|bi\b|warehouse|etl"),
        ("machine learning / ai", r"machine learning|\bai\b|ml\b|nlp|llm|deep learning"),
        ("devops / platform", r"devops|sre|platform|infra|cloud|kubernetes|pipeline"),
        ("qa", r"\bqa\b|test|quality"),
        ("network / systems", r"network|system admin|sysadmin|it support|helpdesk"),
        ("frontend", r"front[- ]?end|ui\b|ux\b|react|angular|vue"),
        ("backend", r"back[- ]?end|api|server"),
        ("mobile", r"mobile|android|ios|react native|flutter"),
        ("database", r"database|dba\b|sql"),
        ("product / business", r"product manager|business analyst|project manager|solutions architect"),
    ]
    for name, pattern in families:
        if re.search(pattern, haystack):
            return name
    return "software engineering" if ROLE_RE.search(title) else None


def build_job_profile(job: Job) -> JobProfile:
    taxonomy = get_taxonomy()
    description = job.description or ""

    def normalise(values: list | str | None) -> tuple[list[str], dict[str, str]]:
        keys: list[str] = []
        labels: dict[str, str] = {}
        for value in values or []:
            phrase = str(value).strip()
            if not phrase:
                continue
            label = normalize_phrase(phrase, taxonomy)
            key = label.lower()
            if key not in keys:
                keys.append(key)
                labels[key] = label
        return keys, labels

    required_keys, required_labels = normalise(job.required_skills)
    preferred_keys, preferred_labels = normalise(job.preferred_skills)

    # Extra evidence from the description (used for certification/education
    # signals and for reporting — never to invent requirements).
    description_skills = extract_skills(description)
    required_certs = sorted({
        skill.label for skill in description_skills
        if taxonomy.category_of(skill.canonical or "") == "certification"
    })

    seniority = None
    for label, pattern in SENIORITY_MARKERS:
        if pattern.search(job.title or ""):
            seniority = label
            break

    return JobProfile(
        job_id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        employment_type=job.employment_type,
        role_family=_role_family(job.title, description),
        seniority=seniority,
        minimum_experience=float(job.minimum_experience) if job.minimum_experience is not None else None,
        description=description,
        required_keys=required_keys,
        required_labels=required_labels,
        preferred_keys=preferred_keys,
        preferred_labels=preferred_labels,
        description_skills=description_skills,
        required_certifications=required_certs,
        education_mentioned=bool(EDUCATION_RE.search(description)),
    )


def match_keys_for_text(text: str) -> tuple[set[str], list[ExtractedSkill]]:
    """Keys/skills for an arbitrary document (used by the semantic baseline)."""
    skills = extract_skills(text)
    return {skill.key for skill in skills}, skills
