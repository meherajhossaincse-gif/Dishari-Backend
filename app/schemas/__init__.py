"""Request/response shapes. These become the OpenAPI schema the frontend generates its types from."""
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")
RoleT = Literal["admin", "teacher", "moderator", "student", "parent"]
LevelT = Literal["K1", "K2", "K3", "K4"]
DiffT = Literal["Easy", "Medium", "Hard"]
LangT = Literal["bn", "en"]
StatusT = Literal["pending", "tagged", "needs_review", "verified", "rejected"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Msg(BaseModel):
    message: str


# ------------------------------------------------------------------ auth / users
class LoginIn(BaseModel):
    email: EmailStr
    password: str
    portal: Literal["staff", "student", "parent", "admin"] | None = None  # login panel the user chose


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    role: Literal["student", "parent", "teacher"] = "student"  # teacher -> pending until an admin approves
    language_pref: LangT = "bn"
    institution: str | None = None
    class_level: str | None = None
    phone: str | None = Field(None, max_length=30)
    child_link_code: str | None = None  # parent: the code shown in the child's profile
    # teacher sign-up extras (stored in the profile)
    subjects: str | None = Field(None, max_length=200)
    classes_taught: str | None = Field(None, max_length=60)
    designation: str | None = Field(None, max_length=80)


class PendingOut(BaseModel):
    pending: bool = True
    message: str
    message_bn: str


class UserOut(ORM):
    id: int
    name: str
    email: str
    roles: list[str]
    status: str
    language_pref: str
    institution: str | None = None
    class_level: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    profile: dict | None = None
    link_code: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=72)


class ChangePwIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


class MeUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    language_pref: LangT | None = None
    institution: str | None = Field(None, max_length=200)
    class_level: str | None = Field(None, max_length=20)
    phone: str | None = Field(None, max_length=30)
    profile: dict[str, str | int | float | bool | list[str] | None] | None = None  # merged into the stored profile


class LinkChildIn(BaseModel):
    link_code: str = Field(min_length=4, max_length=12)


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    roles: list[RoleT] = Field(min_length=1)
    language_pref: LangT = "bn"
    institution: str | None = None
    class_level: str | None = None


class UserUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    roles: list[RoleT] | None = None
    status: Literal["active", "disabled"] | None = None
    language_pref: LangT | None = None
    password: str | None = Field(None, min_length=8, max_length=72)


class ParentLinkIn(BaseModel):
    parent_id: int
    student_id: int


# ------------------------------------------------------------------ taxonomy
class TopicOut(ORM):
    id: str
    concept_id: str
    chapter_id: str
    name_en: str
    name_bn: str
    needs_translation: bool = False


class ConceptOut(ORM):
    id: str
    chapter_id: str
    name_en: str
    name_bn: str
    topics: list[TopicOut] = []


class ChapterOut(ORM):
    id: str
    name_en: str
    name_bn: str
    concepts: list[ConceptOut] = []


class LevelOutS(ORM):
    code: str
    name_en: str
    name_bn: str
    description_en: str
    description_bn: str


class TaxonomyOut(BaseModel):
    version: str
    chapters: list[ChapterOut]
    cognitive_levels: list[LevelOutS]
    difficulties: list[str]
    counts: dict


class RenameIn(BaseModel):
    name_en: str | None = None
    name_bn: str | None = None


# ------------------------------------------------------------------ questions
class Options(BaseModel):
    A: str
    B: str
    C: str
    D: str


class QuestionIn(BaseModel):
    language: LangT
    stem: str = Field(min_length=1)
    options: Options
    answer: Literal["A", "B", "C", "D"] | None = None
    explanation: str | None = None
    source: str | None = None
    figure_dependent: bool = False
    external_id: str | None = None


class TagUpdate(BaseModel):
    chapter_id: str | None = None
    concept_id: str | None = None
    topic_id: str | None = None
    cognitive_level: LevelT | None = None
    difficulty: DiffT | None = None


class QuestionUpdate(TagUpdate):
    stem: str | None = None
    options: Options | None = None
    answer: Literal["A", "B", "C", "D"] | None = None
    explanation: str | None = None
    source: str | None = None
    figure_dependent: bool | None = None


class TagView(BaseModel):
    """Final tag + names in both languages, ready for the UI chips."""
    id: str | None
    name_en: str | None = None
    name_bn: str | None = None
    confidence: float | None = None


class QuestionOut(BaseModel):
    id: int
    external_id: str | None
    language: str
    stem: str
    options: dict
    answer: str | None
    explanation: str | None
    source: str | None
    figure_dependent: bool
    image_url: str | None = None  # relative to the API host, e.g. /media/questions/ab12.png
    status: StatusT
    tag_source: str | None
    chapter: TagView | None
    concept: TagView | None
    topic: TagView | None
    cognitive_level: TagView | None
    difficulty: TagView | None
    min_confidence: float | None
    low_confidence_fields: list[str]
    board_match: dict | None
    model_version: str | None
    warnings: list[str]
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class TagRequest(BaseModel):
    """POST /questions/tag - tag one or many questions (max MAX_BATCH)."""
    items: list[QuestionIn] = Field(min_length=1)
    save: bool = True  # False = preview only (nothing stored)


class TagResult(BaseModel):
    index: int
    ok: bool
    question: QuestionOut | None = None
    prediction: dict | None = None  # raw ML contract output (for save=False)
    error: dict | None = None


# ------------------------------------------------------------------ reviews / reports
class ReviewIn(BaseModel):
    action: Literal["accept", "change", "reject"]
    tags: TagUpdate | None = None  # required for "change"
    reason: str | None = None  # required for "change" and "reject"


class ReviewOut(ORM):
    id: int
    question_id: int
    reviewer_id: int
    action: str
    before: dict
    after: dict
    reason: str | None
    created_at: datetime


class TagReportIn(BaseModel):
    question_id: int
    field: Literal["chapter", "concept", "topic", "cognitive_level", "difficulty", "answer", "other"]
    comment: str = ""


class TagReportOut(ORM):
    id: int
    question_id: int
    reporter_id: int
    field: str
    comment: str
    status: str
    resolved_by: int | None
    resolution_note: str | None
    created_at: datetime


class TagReportResolve(BaseModel):
    status: Literal["resolved", "dismissed"]
    note: str | None = None


# ------------------------------------------------------------------ bulk
class BulkJobOut(ORM):
    id: int
    kind: str
    filename: str
    status: str
    total: int
    processed: int
    succeeded: int
    failed: int
    needs_review: int
    errors: list
    created_at: datetime
    finished_at: datetime | None
    progress: float = 0.0
    result_url: str | None = None


# ------------------------------------------------------------------ board
class BoardQuestionOut(ORM):
    id: int
    board: str
    year: int
    q_no: int | None = None
    language: str
    stem: str
    options: dict
    answer: str | None
    chapter_id: str | None


class BoardQuestionIn(BaseModel):
    board: str = Field(min_length=2, max_length=40)
    year: int = Field(ge=2000, le=2100)
    q_no: int | None = Field(None, ge=1, le=200)
    language: Literal["bn", "en"]
    stem: str = Field(min_length=3)
    options: dict = Field(default_factory=dict)
    answer: Literal["A", "B", "C", "D"] | None = None
    chapter_id: str | None = None


class BoardQuestionPatch(BaseModel):
    board: str | None = Field(None, min_length=2, max_length=40)
    year: int | None = Field(None, ge=2000, le=2100)
    q_no: int | None = Field(None, ge=1, le=200)
    stem: str | None = Field(None, min_length=3)
    options: dict | None = None
    answer: Literal["A", "B", "C", "D"] | None = None
    chapter_id: str | None = None


class BoardMatchDecision(BaseModel):
    status: Literal["confirmed", "dismissed"]


# ------------------------------------------------------------------ papers
class PaperMeta(BaseModel):
    """Header of the printed board-style paper."""
    subject_name: str | None = Field(None, max_length=100)
    subject_code: str | None = Field(None, max_length=6)
    exam_name: str | None = Field(None, max_length=150)
    institution: str | None = Field(None, max_length=200)
    syllabus_year: str | None = Field(None, max_length=8)
    class_level: str | None = Field(None, max_length=20)
    instructions: str | None = Field(None, max_length=600)


class PaperIn(BaseModel):
    title: str
    meta: PaperMeta | None = None
    language: LangT = "bn"
    exam_type: str = "class_test"
    duration_min: int | None = None
    target: dict[LevelT, float] = Field(default_factory=lambda: {"K1": 30, "K2": 30, "K3": 25, "K4": 15})


class PaperUpdate(BaseModel):
    title: str | None = None
    meta: PaperMeta | None = None
    exam_type: str | None = None
    duration_min: int | None = None
    target: dict[LevelT, float] | None = None
    status: Literal["draft", "final"] | None = None


class PaperItemIn(BaseModel):
    question_ids: list[int] = Field(min_length=1)
    marks: float = 1.0


class PaperReorder(BaseModel):
    question_ids: list[int]


class PaperOut(BaseModel):
    id: int
    title: str
    language: str
    exam_type: str
    duration_min: int | None
    target: dict
    meta: dict | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    item_count: int
    items: list[dict] = []


# ------------------------------------------------------------------ practice
class PracticeCreate(BaseModel):
    language: LangT = "bn"
    chapter_ids: list[str] = []
    concept_ids: list[str] = []
    levels: list[LevelT] = []
    size: int = Field(10, ge=1, le=50)
    mode: Literal["practice", "exam"] = "practice"  # exam = timed self-test, no hints
    time_limit_min: int | None = Field(None, ge=1, le=180)


class TestCreate(BaseModel):
    paper_id: int
    title: str | None = None
    instructions: str | None = None
    class_level: str | None = None
    section: str | None = None
    start_at: datetime
    end_at: datetime
    duration_min: int = Field(30, ge=1, le=300)
    shuffle: bool = True
    show_answers: Literal["after_submit", "after_end", "never"] = "after_end"


class TestAnswers(BaseModel):
    answers: dict[int, Literal["A", "B", "C", "D"] | None]


class PracticeSubmit(BaseModel):
    answers: dict[int, Literal["A", "B", "C", "D"] | None]  # {question_id: "A"}
    time_ms: dict[int, int] = {}


class ModelDeployIn(BaseModel):
    version: str
    impl: str = Field(..., description='"mock" or "module:Class", e.g. "dishari_ml.tagger:Tagger"')
    path: str = ""
    metrics: dict = {}
    notes: str = ""
    activate: bool = True
