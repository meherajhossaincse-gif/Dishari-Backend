# Dishari API — endpoint list

Generated from `docs/openapi.json` (run `python scripts/export_openapi.py` after changing routes).
Live docs: **http://localhost:8000/docs**.

## auth

| Method | Path | What it does |
|---|---|---|
| POST | `/v1/auth/login` | Login |
| POST | `/v1/auth/register` | Register — Self sign-up. Students and parents can log in at once (a parent can link a child with the link code). |
| POST | `/v1/auth/refresh` | Refresh |
| POST | `/v1/auth/logout` | Logout — Revokes every token of this user (all devices). |
| GET | `/v1/auth/me` | Me |
| PATCH | `/v1/auth/me` | Update Me |
| POST | `/v1/auth/me/avatar` | Upload Avatar — Profile photo (PNG/JPG/WEBP, max 3 MB). Cropped to a square, 400 px. |
| POST | `/v1/auth/me/link-code` | Regenerate Link Code — New parent-link code (the old one stops working; already linked parents stay linked). |
| GET | `/v1/auth/me/stats` | My Stats — Numbers for the profile page, per role. |
| POST | `/v1/auth/change-password` | Change Password |
| POST | `/v1/auth/forgot-password` | Forgot Password — Always 202 (does not reveal whether the email exists). Email sending is a deployment task |
| POST | `/v1/auth/reset-password` | Reset Password |

## users (admin)

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/users` | List Users |
| POST | `/v1/users` | Create User |
| GET | `/v1/users/{user_id}` | Get User |
| PATCH | `/v1/users/{user_id}` | Update User |
| POST | `/v1/users/parent-links` | Link Parent |

## taxonomy

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/taxonomy` | Full Taxonomy — The whole tree in one call (13 chapters / 91 concepts / 193 topics) - cache it on the frontend. |
| GET | `/v1/taxonomy/chapters` | Chapters |
| GET | `/v1/taxonomy/levels` | Levels — Public: K1-K4 definitions (used on the landing/help page too). |
| PATCH | `/v1/taxonomy/{kind}/{item_id}` | Rename — Admin can rename EN/BN names. IDs never change (the ML model depends on them). |

## questions

| Method | Path | What it does |
|---|---|---|
| POST | `/v1/questions/tag` | Tag Questions — Main entry: send 1..500 MCQs, get tags back. save=false -> preview only (nothing stored). |
| POST | `/v1/questions` | Create One |
| GET | `/v1/questions` | List Questions |
| GET | `/v1/questions/stats` | Stats |
| GET | `/v1/questions/export` | Export Questions — The question bank (with the same filters as the list) as an Excel file with tags. |
| GET | `/v1/questions/{qid}` | Get Question |
| PATCH | `/v1/questions/{qid}` | Update Question — Edit text and/or tags. Text change -> re-tag by model. Manual tag change by a teacher -> needs_review; |
| DELETE | `/v1/questions/{qid}` | Delete Question |
| GET | `/v1/questions/{qid}/history` | History — All model predictions + all moderator reviews for one question (audit trail on the detail page). |
| POST | `/v1/questions/confirm` | Confirm Tags — Tag review: accept the current tags of the chosen questions. A moderator/admin marks them `verified`; |
| POST | `/v1/questions/{qid}/retag` | Retag One |
| POST | `/v1/questions/{qid}/image` | Upload Image — Attach a figure (PNG/JPG/WEBP, max 3 MB). Replaces the old one. Shows as image_url. |
| DELETE | `/v1/questions/{qid}/image` | Remove Image |
| POST | `/v1/questions/extract` | Extract From File — Photo (JPG/PNG/WEBP) or PDF of a question paper -> draft MCQs (Bangla and English). |

## bulk upload

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/bulk/template` | Template |
| POST | `/v1/bulk/upload` | Upload — Starts a background job. Poll GET /bulk/jobs/{id} every 1-2 s until status is done/failed. |
| GET | `/v1/bulk/jobs` | Jobs |
| GET | `/v1/bulk/jobs/{job_id}` | Job |
| GET | `/v1/bulk/jobs/{job_id}/results` | Results |

## board bank

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/board-questions/template` | Template |
| POST | `/v1/board-questions/import` | Import Board |
| GET | `/v1/board-questions` | List Board |
| POST | `/v1/board-questions` | Add Board Question |
| GET | `/v1/board-questions/check` | Check — Live check while a teacher types a question. |
| GET | `/v1/board-questions/matches` | Matches |
| POST | `/v1/board-questions/matches/{match_id}` | Decide |
| PATCH | `/v1/board-questions/{bid}` | Edit Board Question |
| DELETE | `/v1/board-questions/{bid}` | Remove Board Question |

## review

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/reviews/queue` | Queue — Needs-review questions, lowest confidence first, plus questions with open tag reports. |
| POST | `/v1/reviews/{qid}` | Submit Review |
| GET | `/v1/reviews` | Review History |
| POST | `/v1/tag-reports` | Report |
| GET | `/v1/tag-reports` | Reports |
| PATCH | `/v1/tag-reports/{rid}` | Resolve |

## papers (teacher)

| Method | Path | What it does |
|---|---|---|
| POST | `/v1/papers` | Create |
| GET | `/v1/papers` | List Papers |
| GET | `/v1/papers/{pid}` | Get Paper |
| PATCH | `/v1/papers/{pid}` | Update |
| DELETE | `/v1/papers/{pid}` | Delete |
| POST | `/v1/papers/{pid}/items` | Add Items |
| DELETE | `/v1/papers/{pid}/items/{qid}` | Remove Item |
| PUT | `/v1/papers/{pid}/items/order` | Reorder |
| GET | `/v1/papers/{pid}/analysis` | Analysis — Balance of the paper vs its K1-K4 target (W06 analysis panel). |
| POST | `/v1/papers/analyse-upload` | Analyse Upload — Upload an existing question paper (Excel/CSV); get its K1-K4 balance. Nothing is saved. |
| GET | `/v1/papers/{pid}/export` | Export — xlsx = data sheet + analysis; docx = printable Word paper (+ answer key page); |

## OMR (paper exams)

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/papers/{pid}/omr-sheet` | Omr Sheet — Printable OMR answer sheet that matches this paper (number of questions, header). |
| POST | `/v1/papers/{pid}/omr` | Upload Sheets — Upload 1–60 photos/scans of filled OMR sheets. Each is read, graded with its set's key and matched to a studen |
| GET | `/v1/papers/{pid}/omr` | List Results |
| PATCH | `/v1/omr-results/{rid}` | Fix Result — Teacher corrects what the reader saw (set, roll, a bubble, or the student). |
| DELETE | `/v1/omr-results/{rid}` | Delete Result |
| GET | `/v1/papers/{pid}/omr/export` | Export Results |

## practice (student)

| Method | Path | What it does |
|---|---|---|
| POST | `/v1/practice/sessions` | Start — Picks random tagged/verified questions (with an answer key) for the chosen chapters/levels. |
| GET | `/v1/practice/sessions` | My History |
| GET | `/v1/practice/sessions/{sid}` | Get Session |
| POST | `/v1/practice/sessions/{sid}/submit` | Submit |
| GET | `/v1/students/{student_id}/progress` | Progress — W09 progress chart: score per session over time + accuracy per level and chapter. |
| GET | `/v1/students/{student_id}/mastery` | Student Mastery — (R2) Concept mastery using (Σw·correct+1)/(Σw+2); Easy 1, Medium 1.5, Hard 2. |
| GET | `/v1/parents/me/children` | My Children |
| POST | `/v1/parents/me/children` | Link Child — Link a child with the code shown on the child's profile page. |
| DELETE | `/v1/parents/me/children/{sid}` | Unlink Child — Remove a linked child from this parent account (the child's account is not touched). |
| GET | `/v1/students/{student_id}/weekly-report` | Weekly — Saturday-to-Friday report: activity, accuracy vs last week, levels, chapters, weak concepts, suggestions. |

## online tests

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/tests` | List Tests — Staff: tests they created (with attempt counts). Student: tests open to their class, with their state. |
| POST | `/v1/tests` | Create Test |
| GET | `/v1/tests/{tid}/results` | Results |
| DELETE | `/v1/tests/{tid}` | Delete Test |
| POST | `/v1/tests/{tid}/start` | Start |
| PUT | `/v1/tests/{tid}/answers` | Autosave |
| POST | `/v1/tests/{tid}/submit` | Submit |
| GET | `/v1/tests/{tid}/my-result` | My Result |

## school

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/admin/pending-teachers` | Pending Teachers |
| POST | `/v1/users/{uid}/approve` | Approve User |
| POST | `/v1/users/{uid}/reject` | Reject User |
| POST | `/v1/users/{uid}/send-reset` | Admin Send Reset — Admin starts a password reset for a user (same link as 'forgot password'). |
| POST | `/v1/users/{uid}/unlock` | Unlock User |
| GET | `/v1/admin/settings` | Get Settings |
| PUT | `/v1/admin/settings` | Put Settings — Thresholds, upload limits and security. Keys: board_match_threshold, review_threshold, max_upload_rows, |
| GET | `/v1/groups` | List Groups |
| POST | `/v1/groups` | Create Group |
| GET | `/v1/groups/{gid}` | Get Group |
| PATCH | `/v1/groups/{gid}` | Update Group |
| DELETE | `/v1/groups/{gid}` | Delete Group |
| PUT | `/v1/groups/{gid}/members` | Set Members |
| GET | `/v1/students/search` | Search Students — Students a teacher can add to a group (name / email / roll search, optional class + section). |
| POST | `/v1/feedback` | Send Feedback |
| GET | `/v1/feedback/sent` | Sent Feedback |
| GET | `/v1/students/{sid}/feedback` | Student Feedback |
| GET | `/v1/class-report/sources` | Report Sources — What a teacher can make a class report for: their online tests, their papers with OMR sheets, their groups. |
| GET | `/v1/class-report` | Class Report — Average, students, students who need attention (< 50 %), weakest topics, score by level. |
| GET | `/v1/omr/summary` | Omr Summary |
| GET | `/v1/students/{sid}/summary` | Student Summary — Everything the student (and a linked parent) home screen needs in one call. |

## admin, ML & dashboard

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/ml/status` | Ml Status — Frontend shows a banner if status != ready (questions will stay 'pending'). |
| POST | `/v1/ml/predict` | Ml Predict — Raw passthrough to the model using the ML contract (for Razzaque to test the deployed model). |
| GET | `/v1/ml/agreement` | Ml Agreement — Model vs moderator agreement (per field, K1-K4 confusion matrix, precision/recall/F1, by confidence, |
| GET | `/v1/ml/models` | Ml Models — Model versions (read-only, for the AI model page). |
| GET | `/v1/admin/models` | Models |
| POST | `/v1/admin/models` | Deploy Model — Register a model version and (optionally) hot-load it without restarting the server. |
| POST | `/v1/admin/models/reload` | Reload Model |
| POST | `/v1/admin/retag-pending` | Retag Pending — After the real model is deployed, tag every question that was saved while the model was down. |
| GET | `/v1/admin/audit-logs` | Audit Logs |
| GET | `/v1/dashboard` | Dashboard |

## health

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Health |
