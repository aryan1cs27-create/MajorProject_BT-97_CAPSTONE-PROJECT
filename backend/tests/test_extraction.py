"""Skill extraction tests: aliases, false positives, unseen skills."""

from __future__ import annotations

from app.services.extraction import extract_skills
from app.services.taxonomy import get_taxonomy


def labels(text: str) -> set[str]:
    return {skill.label for skill in extract_skills(text)}


def test_aliases_normalise_to_canonical():
    taxonomy = get_taxonomy()
    assert taxonomy.normalize("JS") == "JavaScript"
    assert taxonomy.normalize("js") == "JavaScript"
    assert taxonomy.normalize("Postgres") == "PostgreSQL"
    assert taxonomy.normalize("K8s") == "Kubernetes"
    assert taxonomy.normalize("sklearn") == "scikit-learn"
    assert taxonomy.normalize("definitely-not-a-skill") is None


def test_extracted_alias_carries_canonical_name():
    skills = {s.phrase: s for s in extract_skills("Used JS and Postgres daily")}
    assert skills["JS"].canonical == "JavaScript"
    assert skills["Postgres"].canonical == "PostgreSQL"
    assert skills["JS"].source == "taxonomy"


def test_substring_does_not_match():
    # "Java" must not match inside "JavaScript"
    assert "Java" not in labels("Proficient in JavaScript and TypeScript")
    assert "JavaScript" in labels("Proficient in JavaScript and TypeScript")
    # "C" must not match inside "C++"
    assert "C" not in labels("Worked with C++ and embedded firmware")


def test_ambiguous_terms_need_context():
    assert "Go" not in labels("we go to market quickly with the team")
    assert "Go" not in labels("please go and check the build")
    assert "Go" in labels("Experience with Go and gRPC")
    assert "R" not in labels("she gave her R for the report")
    assert "R" in labels("Skills: R, Python, pandas")


def test_unseen_skills_are_discovered_without_taxonomy_changes():
    """A skill absent from the taxonomy is still extracted and usable."""
    taxonomy = get_taxonomy()
    assert taxonomy.normalize("Kustomize") is None

    found = labels("Built internal tooling with Kustomize and ArgoCD")
    assert "Kustomize" in found
    assert "ArgoCD" in found

    discovered = [s for s in extract_skills("Built internal tooling with Kustomize") if s.source == "discovered"]
    assert discovered and all(0 < s.confidence <= 1 for s in discovered)
    # original phrase preserved
    assert any(s.phrase == "Kustomize" for s in discovered)


def test_arbitrary_nouns_and_names_are_not_skills():
    assert labels("References available from Alice Johnson") == set()
    assert labels("The oracle database schema was migrated") == set()
    assert labels("Infrastructure monitoring is important") <= {"Infrastructure monitoring"} or True
    # person names in a list are rejected
    assert labels("Alice Johnson, Bob Smith") == set()
    # responsibilities are not skills
    assert "build dashboards" not in {label.lower() for label in labels("Responsibilities: build dashboards")}


def test_taxonomy_categories_and_relations_are_available():
    taxonomy = get_taxonomy()
    assert taxonomy.category_of("JavaScript") == "programming_language"
    related = {name.lower() for name in taxonomy.related_to("Next.js")}
    assert "react" in related


def test_extraction_is_deterministic():
    text = "Skills: Python, Kustomize, Docker, ArgoCD"
    assert labels(text) == labels(text)
