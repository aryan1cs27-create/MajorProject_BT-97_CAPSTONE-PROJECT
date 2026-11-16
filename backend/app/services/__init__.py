"""Business-logic services.

* ``storage``     — local upload storage (safe filenames, size limits)
* ``resume_parser`` — PDF/DOCX validation and text extraction
* ``resumes``     — resume use-cases (upload, list, delete)
* ``jobs``        — job CRUD

Matching, recommendations and optional LLM assistance land here in Phase 3+.
"""

__all__ = ["storage", "resume_parser", "resumes", "jobs"]
