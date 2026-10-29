# Local Survey Test Agent

A password-protected Flask application for local survey testing and restricted automation of the designated live questionnaire. Synthetic medical answers are for local fixtures only and must not be sent to a live survey.

## Live Entry spreadsheet intake

The dashboard's Live Entry section uploads an `.xlsx` workbook, automatically matches recognized active-sheet headers to questionnaire fields, and checks values against the saved questionnaire mapping. Use exact visible option labels. For multiple choices, separate labels with commas, semicolons, or newlines; use `None` explicitly when no checkbox option applies. Consent must be `Yes`, `True`, `1`, `Tick`, or `Selected` (case-insensitive). These values record an assertion of consent; the operator must verify that authorization exists. Blank or invalid answers fail the spreadsheet check. A dry run opens the live questionnaire for each eligible row, verifies its structure against the saved fingerprint, applies the row's values, and reads them back; it never clicks final Submit. Use dry runs only with actual authorized responses and consent.

The original workbook is stored in the local SQLite database and protected by dashboard authentication; it is not encrypted by this feature. Each dry-run result is saved individually. Use **Retry failed rows** to retry eligible failures; rows with uncertain or recorded submission attempts are excluded. Once validation is complete, **Submit Entries** opens a review of the exact answers and requires explicit authorization before sending the eligible rows. A successful questionnaire POST followed by the website's visible score page is recorded as `SUBMITTED` by operator-defined policy; it does not independently prove a saved record, and no Form ID is inferred. Attempts are recorded before submission; interrupted or ambiguous outcomes remain `UNKNOWN` and are not retried automatically. Duplicate prevention is per uploaded batch row, not across separate workbooks. The report export adds `Form ID`, `Status`, and `Failure reason` to the active worksheet. Excel processing uses openpyxl, which may not preserve every advanced Excel feature. Restart the application after updating code.

## Windows setup

1. Install Python 3.10 or newer and enable the Python launcher (`py`).
2. Double-click `setup.bat`. It installs Python dependencies and the Chromium runtime, then prompts for a dashboard password. Only a password hash is written to `.env`.
3. Double-click `run_agent.bat`. The dashboard opens at <http://127.0.0.1:8765>.
4. Sign in with the password created during setup.
5. To test browser automation, open **Local form browser**, keep the default local URL, and choose **Open Website in Visible Browser**. Detect fields, configure actual values/options, preview, apply and validate, review the result, then explicitly submit to the local sample form.
6. Enter names yourself into the local browser queue if desired; the queue is stored in this browser's local storage. Handle consent in the visible browser yourself.
7. A submission is marked confirmed only when the sample success page and matching SQLite record are verified. If the page changes after validation, submission is disabled until refreshed and reviewed again.
8. Use **Run history** to filter by name, date, URL, and status, sort, and paginate browser runs. Excel exports four worksheets: Test Records, Field Results, Detected Fields, and Errors.

Keep `.env` and `.app-secret-key` private. Both are excluded from version control. To change the password, run `py setup_password.py` from this folder and restart the app. Do not copy `.env.example` as a working credential file; it contains no usable password hash.

## Running from a terminal

```powershell
py -m pip install -r requirements.txt
py setup_password.py
py app.py
```

The Flask dashboard is deliberately bound to loopback only. Local-form browser sessions block non-loopback requests. Live sessions restrict navigation to the designated questionnaire and reject network resources resolving to non-public addresses. If port `8765` is already occupied, close the other local app and retry. Flask serves requests concurrently, while all synchronous Playwright operations are serialized on a dedicated worker thread so local page requests cannot deadlock the browser-opening request.

## Local test boundaries

- The sample form and batch runner write only to `test_submissions.sqlite3`.
- Local targets accept `localhost` or a loopback IP (`127.0.0.1`, `::1`). The designated live survey host and path are separately supported for inspection and dry validation; other navigation targets are rejected.
- Browser field inspection covers standard HTML inputs, textareas, selects, radio groups, and checkbox groups. Frames and custom widgets are reported, not automatically operated.
- Consent controls require manual operator action. Per-field rules support manual entry, detected options, deterministic random choices with a seed, and skip for optional fields.
- Daily run limit is configurable from 1 to 6. Duplicate test-name + target-URL pairs on the same day are rejected.
- Random choices use detected options only. A configured seed makes the choice sequence reproducible; an empty seed uses an unseeded random generator.
- The browser name queue is stored only in local browser storage and can be edited, reused, cleared, and marked pending/completed/failed/skipped.
- Submission is a separate explicit action after review. `SUBMITTED_CONFIRMED` requires the sample success page plus a matching local SQLite record.
- A `SUBMITTED_LOCAL` status confirms a write to this app's local database only.
- Local records may be removed from the dashboard. The exported workbook includes local IDs, test names, answers, statuses, and local record links.
- Dashboard sessions expire after 30 minutes. POST actions require CSRF tokens.

## Requirement coverage and remaining limits

- **Implemented and tested locally:** loopback target entry and visible browser controls; native field labels/types/options; language selector and manual consent; manually entered names and per-field answer rules; browser validation and review gate; evidence-based local submission confirmation; stop control, daily limit (maximum 6), duplicate prevention; run history/details and Excel export.
- **Not implemented:** unattended processing of the complete name queue (each run is selected and reviewed by the operator); cross-field semantic dependency/contradiction rules in the browser workflow; automatic interaction with frames or custom widgets.
- **Not verified:** whether a visible live score page corresponds to a saved website record or provides a website Form ID. An uncertain attempt requires manual reconciliation; separate workbook uploads are not deduplicated.
- **Not verified on a clean Windows machine:** first-time dependency installation and browser download through `setup.bat`; the instructions and startup checks are present, but validation was run in an environment where dependencies were already installed.

## Validation

Run the local automated checks with:

```powershell
py -m unittest
```

The browser-level test suite uses the installed Playwright Chromium runtime and a temporary local database. No external endpoint is used.
