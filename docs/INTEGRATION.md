# How frontend, backend and ML connect

```
 React frontend (Arshi)  ──HTTP JSON + JWT──▶  FastAPI backend (Jibon)  ──Python call──▶  Tagger (Razzaque)
   localhost:5173                                localhost:8000/v1                         ml_package/ + ml_models/
                                                       │
                                                  PostgreSQL 15 (docker, host port 5433)
```

The three parts agree on four things only. Change any of them only together, and update this file.

| # | Agreement | Where it lives |
|---|-----------|----------------|
| 1 | API paths, request/response JSON | `docs/openapi.json` (auto), `docs/API.md`, live at `/docs` |
| 2 | Error shape `{"error":{"code","message","fields"}}` | `app/core/errors.py` |
| 3 | Taxonomy IDs (`"2"`, `"C02.1"`, `"2.1"`) and K1–K4 / Easy–Medium–Hard labels | `seeds/taxonomy.json`, `GET /v1/taxonomy` |
| 4 | ML input/output | `app/ml/contract.py`, `docs/ML_CONTRACT.md` |

## For the frontend (Arshi)

- Base URL: `http://localhost:8000/v1` (put it in `.env` as `VITE_API_URL`). CORS already allows `localhost:5173` and `localhost:3000`; add others in backend `.env` → `CORS_ORIGINS`.
- Types: `npx openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts`.
- Login: `POST /auth/login {email,password}` → `access_token` (30 min), `refresh_token` (7 days), `user` (with `roles`).
  Send `Authorization: Bearer <access_token>`. On `401` with code `TOKEN_EXPIRED` call `POST /auth/refresh` once, then retry.
- Show menus by `user.roles` (a user can have several, e.g. teacher + moderator).
- Every list endpoint returns `{items, total, page, page_size}` and takes `page`, `page_size`.
- Every tag in a question comes as `{id, name_en, name_bn, confidence}` → pick the name by the UI language.
  `low_confidence_fields` tells which chips to highlight; `status` drives the status chip.
- Taxonomy dropdowns: load `GET /taxonomy` once after login and cache it.
- Bulk upload: `POST /bulk/upload` (multipart `file`) → poll `GET /bulk/jobs/{id}` every 1–2 s → when `status=done` download `result_url`.
- Files (template, export, results) come with `Content-Disposition`; read the blob and save it.
- Question images: `image_url` is relative (`/media/questions/...`) → `<img src={API_HOST + image_url}>`, no token needed.
  Upload with multipart `file` to `POST /questions/{id}/image` (PNG/JPG/WEBP ≤ 3 MB).
- Print / PDF of a paper: fetch `GET /papers/{id}/export?format=html` with the token, open the HTML in a new tab
  (`URL.createObjectURL(new Blob([html], {type: "text/html"}))`); it has a "Print / Save as PDF" button.
- `GET /ml/status` → if `status != "ready"` show a banner: "Model offline, questions will be tagged later".
- Demo logins (password `Dishari@123`): `admin@`, `teacher@`, `moderator@`, `student@`, `parent@` + `dishari.com`.

### Screen → endpoint map

| Screen | Endpoints |
|---|---|
| Login (3 panels: staff / student / parent) / register (student or parent) / forgot | `POST /auth/login` (`portal`), `/auth/register` (`role`, `child_link_code`), `/auth/forgot-password`, `/auth/reset-password` |
| Profile | `GET/PATCH /auth/me` (`profile` fields per role), `POST /auth/me/avatar`, `POST /auth/me/link-code`, `GET /auth/me/stats` |
| Upload: photo / PDF (OCR, Bangla + English) | `POST /questions/extract` → edit drafts → `POST /questions/tag` |
| Board-style one-page paper | `GET /papers/{id}/export?format=html&set=ক|খ|গ|ঘ&embed=true` (in-app preview), `format=docx&set=…`; header via `PATCH /papers/{id}` `meta` (new papers start with school, subject, code, class and year from the teacher profile) |
| OMR answer sheets | `GET /papers/{id}/omr-sheet?set=` (printable sheet, html), `POST /papers/{id}/omr` (form `files`, up to 60 photos/scans), `GET /papers/{id}/omr`, `PATCH /omr-results/{id}` (`set`, `roll`, `student_id`, `answers`), `DELETE /omr-results/{id}`, `GET /papers/{id}/omr/export` (xlsx) |
| Sign-up (teacher pending) / admin approval | `POST /auth/register` (`role: teacher` → `{pending: true}`), login errors `ACCOUNT_PENDING` / `ACCOUNT_REJECTED` / `ACCOUNT_LOCKED`; `GET /admin/pending-teachers`, `POST /users/{id}/approve`, `POST /users/{id}/reject`, `POST /users/{id}/unlock`, `POST /users/{id}/send-reset` |
| Admin settings | `GET/PUT /admin/settings` |
| Board bank | `GET/POST /board-questions`, `PATCH/DELETE /board-questions/{id}`, `POST /board-questions/import` |
| Tag review | `GET /questions?mine=true&status=tagged,needs_review&min_conf_below=&board=&bulk_job_id=`, `POST /questions/confirm`, `PATCH /questions/{id}` |
| Class reports | `GET /class-report/sources`, `GET /class-report?test_id=|paper_id=|group_id=&format=json|html` |
| Groups | `GET/POST /groups`, `GET/PATCH/DELETE /groups/{id}`, `PUT /groups/{id}/members`, `GET /students/search` |
| Feedback | `POST /feedback`, `GET /feedback/sent`, `GET /students/{id}/feedback` |
| Student / parent home | `GET /students/{id}/summary`, `DELETE /parents/me/children/{id}` |
| OMR overview | `GET /omr/summary` |
| Question bank export | `GET /questions/export` (same filters as the list) |
| Online tests | teacher: `POST/GET /tests` (`class_level`, `section`), `GET /tests/{id}/results`; student: `GET /tests`, `POST /tests/{id}/start`, `PUT /tests/{id}/answers`, `POST /tests/{id}/submit`, `GET /tests/{id}/my-result` |
| Weekly report (student, parent) | `GET /students/{id}/weekly-report?offset=&format=json|html` |
| AI model page | `GET /ml/status`, `GET /ml/models`, `GET /ml/agreement`, `POST /ml/predict` |
| Dashboard (all roles) | `GET /dashboard` |
| W03 Tag question | `POST /questions/tag` (`save:false` to preview), `GET /board-questions/check`, `POST/DELETE /questions/{id}/image` |
| W04 Bulk upload | `GET /bulk/template`, `POST /bulk/upload`, `GET /bulk/jobs/{id}`, `GET /bulk/jobs/{id}/results` |
| W05 Question bank | `GET /questions?q=&status=&chapter_id=&level=&difficulty=&language=&board=&mine=`, `GET/PATCH/DELETE /questions/{id}`, `GET /questions/{id}/history` |
| W06 Paper builder | `POST/GET/PATCH /papers`, `POST /papers/{id}/items`, `PUT /papers/{id}/items/order`, `GET /papers/{id}/analysis`, `GET /papers/{id}/export?format=xlsx|docx|html` (html → browser "Save as PDF"), `POST /papers/analyse-upload` |
| W07 Review queue | `GET /reviews/queue`, `POST /reviews/{question_id}` (`accept` / `change`+tags+reason / `reject`+reason) |
| W08 Practice | `POST /practice/sessions`, `GET /practice/sessions/{id}`, `POST /practice/sessions/{id}/submit` |
| W09 Result + progress | submit response, `GET /students/{id}/progress`, `POST /tag-reports` |
| W10 Admin | `GET/POST/PATCH /users`, `PATCH /taxonomy/{chapters|concepts|topics}/{id}`, `POST /board-questions/import`, `GET /admin/audit-logs`, `GET/POST /admin/models`, `GET /ml/agreement` (+`?format=xlsx`) |
| Parent (R2) | `GET /parents/me/children`, `GET /students/{id}/progress`, `GET /students/{id}/mastery` |

## For ML (Razzaque)

See `docs/ML_CONTRACT.md` and `ml_package/README.md`. Until the model is ready the backend uses `TAGGER_IMPL=mock`,
which has the same interface, so frontend and backend work never wait for ML.

## For backend (Jibon)

- Code map: `app/models` (tables) · `app/schemas` (JSON shapes) · `app/api/v1` (routes) · `app/services` (logic) · `app/ml` (model bridge).
- New table/column → edit `app/models/__init__.py` → `docker compose exec api alembic revision --autogenerate -m "..."` → `alembic upgrade head`.
- New/changed route → `python scripts/export_openapi.py` and tell Arshi.
- Tests: `docker compose exec api pytest -q` (uses SQLite, does not touch your data).

## For deployment/testing (Sumaiya)

- Production `.env`: new `SECRET_KEY`, `DEBUG=false`, `SEED_DEMO_USERS=false`, `PRACTICE_ALLOW_NEEDS_REVIEW=false`, real `CORS_ORIGINS`.
- Remove the `--reload` and code volumes from `docker-compose.yml` for production; put HTTPS (nginx/Caddy) in front.
- Password-reset email: `/auth/forgot-password` creates the token; wire an SMTP sender where the TODO is (in DEBUG the token is returned in the response).
- Login is rate-limited (10/min per IP); every write is in `audit_logs`.
