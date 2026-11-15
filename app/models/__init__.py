"""All database tables. Import from here so Alembic sees every model."""
from datetime import datetime, timezone
import enum

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- enums (stored as strings)
class Role(str, enum.Enum):
    admin = "admin"
    teacher = "teacher"
    moderator = "moderator"
    student = "student"
    parent = "parent"


class QStatus(str, enum.Enum):
    pending = "pending"            # model not available yet, waiting to be tagged
    tagged = "tagged"              # model tagged, all confidences >= threshold
    needs_review = "needs_review"  # low confidence or manually edited
    verified = "verified"          # accepted/changed by a moderator
    rejected = "rejected"          # rejected by moderator (bad question)


LEVELS = ["K1", "K2", "K3", "K4"]
DIFFICULTIES = ["Easy", "Medium", "Hard"]
LANGS = ["bn", "en"]


# ---------------------------------------------------------------- users
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled | pending | rejected
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language_pref: Mapped[str] = mapped_column(String(2), default="bn")
    institution: Mapped[str | None] = mapped_column(String(200), nullable=True)
    class_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke all tokens
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)  # role-specific details (see schemas.PROFILE_FIELDS)
    link_code: Mapped[str | None] = mapped_column(String(12), unique=True, nullable=True)  # student -> parent linking

    @property
    def avatar_url(self) -> str | None:
        return f"/media/avatars/{self.avatar_path}" if self.avatar_path else None
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(128), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class ParentLink(Base):
    __tablename__ = "parent_links"
    __table_args__ = (UniqueConstraint("parent_id", "student_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


# ---------------------------------------------------------------- taxonomy (string IDs = same IDs ML uses)
class Chapter(Base):
    __tablename__ = "chapters"
    id: Mapped[str] = mapped_column(String(10), primary_key=True)  # "1".."13"
    name_en: Mapped[str] = mapped_column(String(255))
    name_bn: Mapped[str] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    concepts: Mapped[list["Concept"]] = relationship(back_populates="chapter", order_by="Concept.sort_order")


class Concept(Base):
    __tablename__ = "concepts"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # "C01.1"
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"), index=True)
    name_en: Mapped[str] = mapped_column(String(255))
    name_bn: Mapped[str] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    chapter: Mapped[Chapter] = relationship(back_populates="concepts")
    topics: Mapped[list["Topic"]] = relationship(back_populates="concept", order_by="Topic.sort_order")


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # "1.3.1"
    concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.id"), index=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"), index=True)
    name_en: Mapped[str] = mapped_column(String(255))
    name_bn: Mapped[str] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    needs_translation: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    concept: Mapped[Concept] = relationship(back_populates="topics")


class CognitiveLevel(Base):
    __tablename__ = "cognitive_levels"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)  # K1..K4
    name_en: Mapped[str] = mapped_column(String(60))
    name_bn: Mapped[str] = mapped_column(String(60))
    description_en: Mapped[str] = mapped_column(Text, default="")
    description_bn: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- questions + ML predictions
class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        Index("ix_q_filters", "status", "chapter_id", "cognitive_level"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # client/bulk row id
    language: Mapped[str] = mapped_column(String(2))
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSON)  # {"A": "...", "B": "...", "C": "...", "D": "..."}
    answer: Mapped[str | None] = mapped_column(String(1), nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    figure_dependent: Mapped[bool] = mapped_column(Boolean, default=False)
    image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # file name in storage/media/questions
    stem_hash: Mapped[str] = mapped_column(String(64), index=True)

    # final (current) tags - copied from the model, or set by teacher/moderator
    chapter_id: Mapped[str | None] = mapped_column(ForeignKey("chapters.id"), nullable=True, index=True)
    concept_id: Mapped[str | None] = mapped_column(ForeignKey("concepts.id"), nullable=True, index=True)
    topic_id: Mapped[str | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    cognitive_level: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    difficulty: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tag_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # model | teacher | moderator
    min_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default=QStatus.pending.value, index=True)
    is_board: Mapped[bool] = mapped_column(Boolean, default=False)
    board_question_id: Mapped[int | None] = mapped_column(ForeignKey("board_questions.id"), nullable=True)
    bulk_job_id: Mapped[int | None] = mapped_column(ForeignKey("bulk_jobs.id"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    predictions: Mapped[list["ModelPrediction"]] = relationship(
        back_populates="question", order_by="ModelPrediction.id.desc()", cascade="all, delete-orphan")


class ModelPrediction(Base):
    """Every model output is stored unchanged (raw = the exact ML contract JSON)."""
    __tablename__ = "model_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(60))
    chapter_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    chapter_conf: Mapped[float | None] = mapped_column(Float, nullable=True)
    concept_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    concept_conf: Mapped[float | None] = mapped_column(Float, nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    topic_conf: Mapped[float | None] = mapped_column(Float, nullable=True)
    cognitive_level: Mapped[str | None] = mapped_column(String(2), nullable=True)
    cognitive_conf: Mapped[float | None] = mapped_column(Float, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(10), nullable=True)
    difficulty_conf: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    question: Mapped[Question] = relationship(back_populates="predictions")


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(60), unique=True)
    impl: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(500), default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"macro_f1_level": 0.71, ...}
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    deployed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- board bank
class BoardQuestion(Base):
    __tablename__ = "board_questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    board: Mapped[str] = mapped_column(String(40))  # Dhaka, Rajshahi, ...
    year: Mapped[int] = mapped_column(Integer)
    q_no: Mapped[int | None] = mapped_column(Integer, nullable=True)  # question number in that board paper
    language: Mapped[str] = mapped_column(String(2))
    stem: Mapped[str] = mapped_column(Text)
    stem_norm: Mapped[str] = mapped_column(Text)
    stem_hash: Mapped[str] = mapped_column(String(64), unique=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    answer: Mapped[str | None] = mapped_column(String(1), nullable=True)
    chapter_id: Mapped[str | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BoardMatch(Base):
    __tablename__ = "board_matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    board_question_id: Mapped[int] = mapped_column(ForeignKey("board_questions.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="suggested")  # suggested | confirmed | dismissed
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- review + reports
class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(10))  # accept | change | reject
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TagReport(Base):
    __tablename__ = "tag_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    field: Mapped[str] = mapped_column(String(20))  # chapter | concept | topic | cognitive_level | difficulty | answer | other
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | resolved | dismissed
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- bulk jobs
class BulkJob(Base):
    __tablename__ = "bulk_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="questions")  # questions | board_bank
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | running | done | failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    needs_review: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)  # [{"row": 5, "code": "EMPTY_STEM", "message": "..."}]
    result_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- papers (teacher)
class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(2), default="bn")
    exam_type: Mapped[str] = mapped_column(String(40), default="class_test")
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target: Mapped[dict] = mapped_column(JSON, default=dict)  # {"K1":30,"K2":30,"K3":25,"K4":15} percent
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | final
    # print header: subject_name, subject_code, exam_name, syllabus_year, institution, instructions, class_level
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    items: Mapped[list["PaperItem"]] = relationship(
        back_populates="paper", order_by="PaperItem.position", cascade="all, delete-orphan")


class PaperItem(Base):
    __tablename__ = "paper_items"
    __table_args__ = (UniqueConstraint("paper_id", "question_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    marks: Mapped[float] = mapped_column(Float, default=1.0)
    paper: Mapped[Paper] = relationship(back_populates="items")
    question: Mapped[Question] = relationship()


# ---------------------------------------------------------------- practice (student)
class PracticeSession(Base):
    __tablename__ = "practice_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    language: Mapped[str] = mapped_column(String(2), default="bn")
    filters: Mapped[dict] = mapped_column(JSON, default=dict)  # {"chapter_ids": [...], "levels": [...]}
    status: Mapped[str] = mapped_column(String(12), default="in_progress")  # in_progress | submitted
    total: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answers: Mapped[list["PracticeAnswer"]] = relationship(
        back_populates="session", order_by="PracticeAnswer.position", cascade="all, delete-orphan")


class PracticeAnswer(Base):
    __tablename__ = "practice_answers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("practice_sessions.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    position: Mapped[int] = mapped_column(Integer)
    chosen: Mapped[str | None] = mapped_column(String(1), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session: Mapped[PracticeSession] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship()


# ---------------------------------------------------------------- system
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Setting(Base):
    """Admin-editable runtime settings (key/value), e.g. review_threshold."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


# ---------------------------------------------------------------- online tests (teacher-assigned, timed)
class Test(Base):
    __tablename__ = "tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    class_level: Mapped[str | None] = mapped_column(String(20), nullable=True)  # None = every student
    section: Mapped[str | None] = mapped_column(String(20), nullable=True)  # None = every section of that class
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_min: Mapped[int] = mapped_column(Integer)
    shuffle: Mapped[bool] = mapped_column(Boolean, default=True)
    show_answers: Mapped[str] = mapped_column(String(15), default="after_end")  # after_submit | after_end | never
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paper: Mapped[Paper] = relationship()


class TestAttempt(Base):
    __tablename__ = "test_attempts"
    __table_args__ = (UniqueConstraint("test_id", "student_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question_order: Mapped[list] = mapped_column(JSON, default=list)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)  # {"<question_id>": "A"}
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    late: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    test: Mapped[Test] = relationship()


# ---------------------------------------------------------------- OMR (paper exam, scanned answer sheets)
class OmrResult(Base):
    __tablename__ = "omr_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255), default="")
    set_code: Mapped[str | None] = mapped_column(String(1), nullable=True)  # A-D (= ক-ঘ)
    roll: Mapped[str | None] = mapped_column(String(12), nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)  # {"1": "A", ...} in the sheet's order
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    image_url: Mapped[str | None] = mapped_column(String(255), nullable=True)  # annotated sheet
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- groups + teacher feedback
class StudentGroup(Base):
    """A teacher's group of students (a class section, a batch, a coaching group)."""
    __tablename__ = "student_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    class_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    section: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "student_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("student_groups.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class Feedback(Base):
    """Teacher feedback to one student (seen by the student and the linked parents)."""
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    context: Mapped[str | None] = mapped_column(String(200), nullable=True)  # e.g. "Model Test 3"
    message: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
