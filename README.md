# Dishari Backend (FastAPI + PostgreSQL)

Bilingual (বাংলা/English) SSC Physics MCQ tagging API: chapter → concept → topic, cognitive level K1–K4,
difficulty, board-question match, review workflow, paper builder, student practice and progress.

## চালানোর নিয়ম (Windows, Docker Desktop চালু থাকতে হবে)

```powershell
cd "D:\Dishari Backend"
copy .env.example .env        # প্রথমবার একবারই
docker compose up --build     # প্রথমবার ৩–৫ মিনিট লাগবে
```

তারপর খুলুন:

| কী | ঠিকানা |
|---|---|
| API ডকুমেন্টেশন (সব endpoint চেষ্টা করা যায়) | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |
| Database দেখা (Adminer) | http://localhost:8080 → System: PostgreSQL, Server: `db`, User/Password/Database: `dishari` |

Demo login (password `Dishari@123`): `admin@dishari.com`, `teacher@dishari.com`, `moderator@dishari.com`,
`student@dishari.com`, `parent@dishari.com`. In /docs: click **Authorize**, or call `POST /v1/auth/login` and paste the token.

বন্ধ: `Ctrl+C`, তারপর `docker compose down`. সব ডেটা মুছে নতুন করে: `docker compose down -v`.

Tests: `docker compose exec api pytest -q` (25 tests, SQLite, your data is not touched).

## What's inside

| Folder | What |
|---|---|
| `app/models` | 21 tables (users, taxonomy, questions, model_predictions, board bank, reviews, tag_reports, papers, practice, bulk_jobs, audit_logs, ...) |
| `app/api/v1` | 61 endpoints, grouped by screen (see `docs/API.md`) |
| `app/ml` | ML bridge: contract, loader, mock tagger, board matcher |
| `app/services` | tagging pipeline, bulk jobs, Excel, paper analysis, practice scoring, mastery (R2), seed |
| `alembic/` | database migrations (run automatically on start) |
| `seeds/` | `taxonomy.json` (13 chapters / 91 concepts / 193 topics, EN+BN), 16 handmade demo questions |
| `ml_package/`, `ml_models/` | where the real model goes (template included) |
| `scripts/` | contract checker, OpenAPI export, board-bank import, create admin |
| `docs/` | `INTEGRATION.md` (read first), `API.md`, `ML_CONTRACT.md`, `openapi.json` |

## Added on 13 Nov (second round)

- Question figures: `POST /v1/questions/{id}/image` → `image_url` shown to teachers and students.
- Printable paper: `GET /v1/papers/{id}/export?format=docx` (Word) or `format=html` (print / Save as PDF, proper Bangla).
- Model vs moderator agreement for the report: `GET /v1/ml/agreement` (`?format=xlsx`, `?model_version=`).
- The 16 topics that had a name in only one gold file now have both names in `seeds/taxonomy.json`.

## Added on 13 Nov (third round)

- Login has 3 panels (`portal`: staff / student / parent); parents can sign up and link a child with the child's 6-letter code.
- Rich profiles per role (`users.profile`), profile photo, profile statistics.
- Online tests from a paper (time window, timer, autosave, results), student mock tests (`mode: exam`).
- Weekly report (Saturday–Friday) for students and parents, printable.
- Photo/PDF question-paper upload with free offline OCR (Tesseract `ben+eng`, installed in the Docker image).
- Board-style one-page MCQ paper (subject-code boxes, set ক/খ/গ/ঘ with its own order and answer key) for print/PDF; assign a paper online to a class + section.
- Migrations `0003`, `0004` run automatically on start. Rebuild once: `docker compose up --build`.

## Added on 13 Nov (fourth round)

- The paper header fills itself from the teacher's profile (school, subject, subject code, class, year); every field stays editable.
- OMR: print an OMR sheet for any paper (sets ক/খ/গ/ঘ), upload photos or scans of filled sheets (many at once). Roll, set and answers are read with OpenCV, graded against that set's key, and matched to students by class + roll. Wrong reads can be fixed by hand; results export to Excel and show up in the weekly report.
- Migration `0005` (`omr_results`). Rebuild once: `docker compose up --build`.

## Added on 14 Nov (design-file round)

- Teachers can sign up themselves. The account stays **pending** until an admin approves it (Users page / admin dashboard). A rejected teacher sees the reason when logging in.
- Login has 4 panels: Teacher, Student, Parent, Admin. Locking after repeated wrong passwords (admin can change or unlock).
- Admin settings saved in the database: board-match and low-confidence thresholds, upload limits, session length, lockout, rate limit.
- Board bank: add / edit / remove single questions, with question number.
- Teacher tools: tag review (confirm or fix tags), OMR results overview, class reports (weakest topics, score by level, students needing attention, printable PDF), groups of students, feedback to students and parents, question bank Excel export.
- Student and parent home: latest result with class average, weak topics, progress, teacher feedback, upcoming exam. Parents can remove a linked child.
- Migration `0006`. Rebuild once: `docker compose up --build`.

## Key rules (from the SRS)

- Any tag confidence < **0.60** → *Needs review*; board match similarity ≥ **0.85**.
- Model down → question saved as *Pending*, tagged later by `POST /v1/admin/retag-pending`.
- Moderator-verified tags are never overwritten by the model.
- Change / reject in review needs a reason; every write goes to `audit_logs`.
- Mastery (R2): `(Σw·correct + 1) / (Σw + 2)`, w = Easy 1, Medium 1.5, Hard 2; < 3 answers = insufficient evidence.

## Running without Docker (optional)

Python 3.11 or 3.12 (not 3.14), a Postgres database, then:

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
# in .env set DATABASE_URL=postgresql+psycopg://dishari:dishari@localhost:5433/dishari
alembic upgrade head
uvicorn app.main:app --reload
```
