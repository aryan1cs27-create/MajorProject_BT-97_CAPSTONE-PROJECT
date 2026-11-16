"""SQLAlchemy models.

Importing this package registers every table on ``app.db.Base`` so
``Base.metadata.create_all`` picks them up.
"""

from app.models.feedback import MatchFeedback
from app.models.job import Job
from app.models.match import MatchResult
from app.models.resume import (
    STATUS_FAILED,
    STATUS_NO_TEXT,
    STATUS_PENDING,
    STATUS_PROCESSED,
    Resume,
)

__all__ = [
    "Job",
    "Resume",
    "MatchResult",
    "MatchFeedback",
    "STATUS_PENDING",
    "STATUS_PROCESSED",
    "STATUS_NO_TEXT",
    "STATUS_FAILED",
]
