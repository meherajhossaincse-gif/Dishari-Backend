"""Role-specific profile fields, parent link codes and the profile statistics."""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (Paper, ParentLink, PracticeAnswer, PracticeSession, Question, Review, TagReport, Test,
                        TestAttempt, User)

# keys a user may store in users.profile (values are short text / numbers / lists of text)
PROFILE_FIELDS: dict[str, set[str]] = {
    "common": {"bio", "district", "address", "gender", "notify_email", "notify_weekly_report",
               # settings page (all roles)
               "pref_digits", "pref_text_size", "notify_in_app", "notify_frequency", "notify_tagging_done",
               "notify_omr_done", "notify_weekly_summary", "notify_new_result", "notify_exam_reminder",
               "notify_teacher_feedback"},
    "teacher": {"designation", "subjects", "classes_taught", "experience_years", "teacher_id", "qualification",
                "department", "tag_default_lang", "tag_highlight_low", "tag_suggest_board", "exam_marks_per_q",
                "exam_paper_lang", "exam_negative"},
    "moderator": {"designation", "subjects", "expertise", "experience_years"},
    "admin": {"designation", "department"},
    "student": {"date_of_birth", "section", "roll", "group", "board", "ssc_year", "guardian_name", "guardian_phone",
                "goal", "favourite_subject"},
    "parent": {"relation", "occupation", "alt_phone"},
}

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion


def new_link_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        if not db.scalar(select(User.id).where(User.link_code == code)):
            return code


def _streak(days: set) -> int:
    d, n = datetime.now(timezone.utc).date(), 0
    if d not in days:
        d -= timedelta(days=1)
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def student_activity_days(db: Session, student_id: int) -> set:
    rows = db.scalars(select(PracticeSession.submitted_at).where(PracticeSession.student_id == student_id,
                                                                PracticeSession.submitted_at.is_not(None))).all()
    rows += db.scalars(select(TestAttempt.submitted_at).where(TestAttempt.student_id == student_id,
                                                             TestAttempt.submitted_at.is_not(None))).all()
    return {(r if r.tzinfo else r.replace(tzinfo=timezone.utc)).date() for r in rows}


def user_stats(db: Session, u: User) -> dict:
    out: dict = {"member_since": u.created_at, "last_login": u.last_login_at}
    r = set(u.roles)
    if r & {"teacher", "moderator", "admin"}:
        out["teacher"] = {
            "questions": db.scalar(select(func.count(Question.id)).where(Question.created_by == u.id, Question.deleted.is_(False))),
            "verified": db.scalar(select(func.count(Question.id)).where(Question.created_by == u.id, Question.status == "verified")),
            "papers": db.scalar(select(func.count(Paper.id)).where(Paper.owner_id == u.id)),
            "tests": db.scalar(select(func.count(Test.id)).where(Test.created_by == u.id)),
            "test_takers": db.scalar(select(func.count(TestAttempt.id)).join(Test, Test.id == TestAttempt.test_id)
                                     .where(Test.created_by == u.id, TestAttempt.submitted_at.is_not(None))),
        }
    if r & {"moderator", "admin"}:
        by = dict(db.execute(select(Review.action, func.count()).where(Review.reviewer_id == u.id).group_by(Review.action)).all())
        out["moderator"] = {"reviews": sum(by.values()), "accepted": by.get("accept", 0), "changed": by.get("change", 0),
                            "rejected": by.get("reject", 0),
                            "reports_resolved": db.scalar(select(func.count(TagReport.id)).where(TagReport.resolved_by == u.id))}
    if "student" in r:
        total = db.scalar(select(func.count(PracticeAnswer.id)).join(PracticeSession, PracticeSession.id == PracticeAnswer.session_id)
                          .where(PracticeSession.student_id == u.id, PracticeSession.status == "submitted")) or 0
        correct = db.scalar(select(func.count(PracticeAnswer.id)).join(PracticeSession, PracticeSession.id == PracticeAnswer.session_id)
                            .where(PracticeSession.student_id == u.id, PracticeAnswer.is_correct.is_(True))) or 0
        out["student"] = {
            "practice_sets": db.scalar(select(func.count(PracticeSession.id)).where(PracticeSession.student_id == u.id, PracticeSession.status == "submitted")),
            "questions_answered": total, "accuracy": round(correct / total, 3) if total else None,
            "tests_taken": db.scalar(select(func.count(TestAttempt.id)).where(TestAttempt.student_id == u.id, TestAttempt.submitted_at.is_not(None))),
            "streak_days": _streak(student_activity_days(db, u.id)),
            "parents_linked": db.scalar(select(func.count(ParentLink.id)).where(ParentLink.student_id == u.id)),
        }
    if "parent" in r:
        out["parent"] = {"children": db.scalar(select(func.count(ParentLink.id)).where(ParentLink.parent_id == u.id))}
    if "admin" in r:
        out["admin"] = {"users": db.scalar(select(func.count(User.id))),
                        "questions": db.scalar(select(func.count(Question.id)).where(Question.deleted.is_(False)))}
    return out


SUBJECT_CODES = {"physics": "136", "পদার্থবিজ্ঞান": "136", "chemistry": "137", "রসায়ন": "137", "biology": "138",
                 "জীববিজ্ঞান": "138", "higher math": "126", "উচ্চতর গণিত": "126", "math": "109", "গণিত": "109",
                 "ict": "154", "তথ্য ও যোগাযোগ প্রযুক্তি": "154", "general science": "127", "বিজ্ঞান": "127"}


def paper_header_defaults(user: User, lang: str = "bn") -> dict:
    """Print header filled from the teacher's profile; the teacher can still edit every field."""
    prof = user.profile or {}
    subj = str(prof.get("subjects") or "").split(",")[0].strip() or ("পদার্থবিজ্ঞান" if lang == "bn" else "Physics")
    cls = str(prof.get("classes_taught") or "").split(",")[0].strip()
    from datetime import datetime
    return {"institution": user.institution or "", "subject_name": subj,
            "subject_code": SUBJECT_CODES.get(subj.lower(), SUBJECT_CODES.get(subj, "136")),
            "class_level": "".join(ch for ch in cls if ch.isdigit()) or cls,
            "syllabus_year": str(datetime.now().year)}
