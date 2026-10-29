"""Authenticated spreadsheet intake, reviewed submission, and reporting."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import zipfile

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from openpyxl import load_workbook
from consent import affirmative_consent
from browser_agent import DryRunFailure

live_batches = Blueprint('live_batches', __name__)
db = None
browser_worker = None
batch_lock = threading.Lock()
active_batch_id = None

FIELDS = ('Full Name', 'Promoter', 'Age', 'Gender', 'Both_Legs_Swelling',
          'Evening_Swelling', 'Body_Swelling', 'Morning_Face_Swelling',
          'Breathing_Difficulty', 'Breathing_Lying_Down', 'Breathing_Walking',
          'Clinical_Conditions', 'Past_Disorders', 'Consent')
MULTI_FIELDS = {'Clinical_Conditions', 'Past_Disorders'}
REPORT_COLUMNS = {'form id', 'status', 'failure reason'}
MAX_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED = 80 * 1024 * 1024
# The operator-defined outcome is the site's visible score result. It is not a
# claim that a server-side record or website Form ID has been verified.
VERIFIED_CONFIRMATION_SOURCES = frozenset({'score-page'})

HEADER_ALIASES = {
    'Promoter': ('Select Promoter',),
    'Breathing_Difficulty': ('A. Do you have breathing difficulty.',),
    'Consent': ('I Consent',),
}


def normalize_header(value):
    return ' '.join(str(value).split()).casefold()


def resolve_headers(headers, definition):
    aliases = {}
    for field in definition['fields']:
        logical = field['column']
        for text in (logical, field['label'], *HEADER_ALIASES.get(logical, ())):
            aliases.setdefault(normalize_header(text), set()).add(logical)
    candidates = {field: [] for field in FIELDS}
    errors = []
    seen = {}
    for header in headers:
        if not header:
            continue
        normalized = normalize_header(header)
        if normalized in REPORT_COLUMNS:
            continue
        seen.setdefault(normalized, []).append(header)
        matches = aliases.get(normalized, set())
        if len(matches) > 1:
            errors.append(f'Header {header!r} is ambiguous.')
        elif matches:
            candidates[next(iter(matches))].append(header)
    for values in seen.values():
        if len(values) > 1:
            errors.append('Duplicate headers after whitespace and case normalization: ' + ', '.join(values))
    resolved = {}
    for field, matches in candidates.items():
        if len(matches) == 1:
            resolved[field] = matches[0]
        elif not matches:
            errors.append(f'{field}: no matching header.')
        else:
            errors.append(f'{field}: multiple matching headers; choose one explicitly.')
    return resolved, errors


def saved_mapping():
    with (Path(__file__).resolve().parent / 'questionnaire_mapping.json').open(encoding='utf-8') as source:
        mapping = json.load(source)
    fields = mapping['fields']
    encoded = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if hashlib.sha256(encoded).hexdigest() != mapping['fingerprint'] or [f['column'] for f in fields] != list(FIELDS):
        raise ValueError('Saved questionnaire mapping failed its integrity check.')
    return mapping


def configure(database, worker):
    global db, browser_worker
    db = database
    browser_worker = worker


def init_tables(con):
    con.execute('''CREATE TABLE IF NOT EXISTS live_batches (
        id INTEGER PRIMARY KEY, filename TEXT NOT NULL, workbook BLOB NOT NULL,
        sheet_name TEXT NOT NULL, headers TEXT NOT NULL, header_mapping TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS live_batch_rows (
        batch_id INTEGER NOT NULL, row_number INTEGER NOT NULL, status TEXT NOT NULL,
        updated_at TEXT NOT NULL, form_id TEXT, error TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(batch_id, row_number), FOREIGN KEY(batch_id) REFERENCES live_batches(id))''')
    con.execute('''CREATE TABLE IF NOT EXISTS live_submission_attempts (
        id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, row_number INTEGER NOT NULL,
        entry_digest TEXT NOT NULL UNIQUE, state TEXT NOT NULL, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', website_form_id TEXT,
        UNIQUE(batch_id, row_number), FOREIGN KEY(batch_id) REFERENCES live_batches(id))''')
    if 'attempted_submit' not in {row[1] for row in con.execute('PRAGMA table_info(live_batch_rows)')}:
        con.execute('ALTER TABLE live_batch_rows ADD COLUMN attempted_submit INTEGER NOT NULL DEFAULT 0')
    # After an interrupted run, an in-flight submission cannot safely be retried.
    con.execute("UPDATE live_batch_rows SET status='UNKNOWN', error='Interrupted while submitting; verify externally before retrying.' WHERE status='SUBMITTING'")
    con.execute("UPDATE live_submission_attempts SET state='UNKNOWN', updated_at=? WHERE state='SUBMITTING'", (now(),))
    con.execute("UPDATE live_batch_rows SET status='PENDING', error='Dry run interrupted; it can be started again.' WHERE status='VALIDATING'")


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def has_value(cell):
    value = cell.value
    return value is not None and (not isinstance(value, str) or bool(value.strip()) or cell.data_type == 'f')


def populated_layout(sheet):
    # Loaded cells include formatting-only cells. Inspect sparse cells rather than
    # expanding the reported rectangle, which can reach Excel's full dimensions.
    populated = [cell for cell in sheet._cells.values() if has_value(cell)]
    if not populated:
        raise ValueError('The worksheet contains no data.')
    rows = {}
    for cell in populated:
        rows.setdefault(cell.row, []).append(cell)
    expected = {name.casefold() for name in FIELDS}
    scores = {number: sum(isinstance(cell.value, str) and cell.data_type != 'f' and
                          cell.value.strip().casefold() in expected for cell in cells)
              for number, cells in rows.items()}
    best_score = max(scores.values())
    if best_score >= 2:
        header_row = min(number for number, score in scores.items() if score == best_score)
    else:
        # A single-cell title may precede an entirely custom set of headers.
        candidates = sorted(rows)
        header_row = candidates[0]
        if len(rows[header_row]) == 1:
            header_row = next((number for number in candidates if len(rows[number]) >= 2), header_row)
    if any(len(cells) > 1 for number, cells in rows.items() if number < header_row):
        raise ValueError('Unable to identify the header row safely; remove or simplify rows above the headers.')
    columns = {cell.column for cell in populated}
    data_rows = sorted(number for number in rows if number > header_row)
    if len(data_rows) > 20000 or len(columns) > 256:
        raise ValueError('Workbook exceeds the 20,000 populated data rows or 256 populated columns limit.')
    if any(cell.data_type == 'f' for cell in rows[header_row]):
        raise ValueError('Formula-based column headers are not supported; use explicit header text.')
    max_column = max(columns)
    headers = [''] * max_column
    for cell in rows[header_row]:
        headers[cell.column - 1] = str(cell.value).strip()
    nonempty = [header for header in headers if header]
    if not nonempty or len(nonempty) != len(set(nonempty)):
        raise ValueError('Column headers must be present and unique.')
    return {'header_row': header_row, 'data_rows': data_rows,
            'column_count': len(columns), 'last_column': max_column, 'headers': headers}


def open_workbook(data):
    if len(data) > MAX_BYTES:
        raise ValueError('Workbook exceeds the 10 MB upload limit.')
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED:
                raise ValueError('Workbook expands beyond the permitted size.')
        book = load_workbook(BytesIO(data), data_only=False, keep_links=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise ValueError('The file is not a supported .xlsx workbook or exceeds the size limit.') from exc
    sheet = book.active
    try:
        layout = populated_layout(sheet)
    except Exception:
        book.close()
        raise
    return book, sheet, layout


def get_batch(batch_id):
    with db() as con:
        row = con.execute('SELECT * FROM live_batches WHERE id=?', (batch_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@live_batches.route('/live-entry')
def index():
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    filename = request.args.get('filename', '').strip()
    clauses, params = [], []
    if from_date:
        clauses.append('substr(b.created_at, 1, 10) >= ?')
        params.append(from_date)
    if to_date:
        clauses.append('substr(b.created_at, 1, 10) <= ?')
        params.append(to_date)
    if filename:
        clauses.append('b.filename LIKE ?')
        params.append(f'%{filename}%')
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with db() as con:
        batches = con.execute(f'''
            SELECT b.id, b.filename, b.created_at,
                   COUNT(r.row_number) AS total_rows,
                   SUM(CASE WHEN r.status='VALIDATED' THEN 1 ELSE 0 END) AS validated_rows,
                   SUM(CASE WHEN r.attempted_submit=1 OR r.status IN ('SUBMITTING','SUBMITTED','UNKNOWN') THEN 1 ELSE 0 END) AS attempted_rows,
                   SUM(CASE WHEN r.status='SUBMITTED' THEN 1 ELSE 0 END) AS submitted_rows,
                   SUM(CASE WHEN r.status='FAILED' THEN 1 ELSE 0 END) AS failed_rows,
                   SUM(CASE WHEN r.status='UNKNOWN' THEN 1 ELSE 0 END) AS unknown_rows
            FROM live_batches b
            LEFT JOIN live_batch_rows r ON r.batch_id=b.id
            {where}
            GROUP BY b.id
            ORDER BY b.id DESC
        ''', params).fetchall()
        totals = con.execute(f'''
            SELECT COUNT(DISTINCT b.id) AS files,
                   COUNT(r.row_number) AS total_rows,
                   SUM(CASE WHEN r.status='VALIDATED' THEN 1 ELSE 0 END) AS validated_rows,
                   SUM(CASE WHEN r.attempted_submit=1 OR r.status IN ('SUBMITTING','SUBMITTED','UNKNOWN') THEN 1 ELSE 0 END) AS attempted_rows,
                   SUM(CASE WHEN r.status='SUBMITTED' THEN 1 ELSE 0 END) AS submitted_rows,
                   SUM(CASE WHEN r.status='FAILED' THEN 1 ELSE 0 END) AS failed_rows,
                   SUM(CASE WHEN r.status='UNKNOWN' THEN 1 ELSE 0 END) AS unknown_rows
            FROM live_batches b
            LEFT JOIN live_batch_rows r ON r.batch_id=b.id
            {where}
        ''', params).fetchone()
    return render_template('live_entry.html', batches=batches, fields=FIELDS,
                           from_date=from_date, to_date=to_date, filename=filename, totals=totals)


@live_batches.route('/live-entry/day/<day>')
def day_summary(day):
    try:
        selected_day = datetime.strptime(day, '%Y-%m-%d').date()
    except ValueError:
        abort(404)
    india = ZoneInfo('Asia/Kolkata')
    with db() as con:
        rows = con.execute("""
            SELECT b.id AS batch_id, b.filename, b.created_at, r.status
            FROM live_batches b
            LEFT JOIN live_batch_rows r ON r.batch_id=b.id
            ORDER BY b.created_at DESC
        """).fetchall()
    files = {}
    totals = {'files': 0, 'rows': 0, 'submitted': 0, 'validated': 0, 'failed': 0, 'unknown': 0}
    for row in rows:
        try:
            row_day = datetime.fromisoformat(row['created_at']).astimezone(india).date()
        except (TypeError, ValueError):
            try:
                row_day = datetime.strptime(str(row['created_at'])[:10], '%Y-%m-%d').date()
            except ValueError:
                continue
        if row_day != selected_day:
            continue
        item = files.setdefault(row['batch_id'], {
            'id': row['batch_id'], 'filename': row['filename'], 'created_at': row['created_at'],
            'rows': 0, 'submitted': 0, 'validated': 0, 'failed': 0, 'unknown': 0,
        })
        if row['status'] is not None:
            item['rows'] += 1
            item['submitted'] += int(row['status'] == 'SUBMITTED')
            item['validated'] += int(row['status'] == 'VALIDATED')
            item['failed'] += int(row['status'] == 'FAILED')
            item['unknown'] += int(row['status'] == 'UNKNOWN')
    file_list = sorted(files.values(), key=lambda item: item['created_at'], reverse=True)
    totals['files'] = len(file_list)
    for item in file_list:
        for key in ('rows', 'submitted', 'validated', 'failed', 'unknown'):
            totals[key] += item[key]
    return render_template('live_entry_day.html', day=selected_day,
                           day_label=selected_day.strftime('%A, %d %B %Y'),
                           files=file_list, totals=totals)


@live_batches.route('/live-entry/upload', methods=['POST'])
def upload():
    upload_file = request.files.get('workbook')
    if not upload_file or not upload_file.filename.lower().endswith('.xlsx'):
        flash('Choose an .xlsx workbook.')
        return redirect(url_for('live_batches.index'))
    data = upload_file.stream.read(MAX_BYTES + 1)
    try:
        book, sheet, layout = open_workbook(data)
        headers = layout['headers']
        row_numbers = layout['data_rows']
        if not row_numbers:
            raise ValueError('The workbook has no non-empty data rows.')
        filename = upload_file.filename.replace('\\', '/').rsplit('/', 1)[-1][:255]
        with db() as con:
            cur = con.execute('INSERT INTO live_batches (filename,workbook,sheet_name,headers,created_at) VALUES (?,?,?,?,?)',
                              (filename, data, sheet.title, json.dumps(headers), now()))
            batch_id = cur.lastrowid
            con.executemany('INSERT INTO live_batch_rows (batch_id,row_number,status,updated_at) VALUES (?,?,?,?)',
                            [(batch_id, number, 'PENDING', now()) for number in row_numbers])
        book.close()
        resolved, errors = resolve_headers(headers, saved_mapping())
        if not errors:
            validate_batch(batch_id, resolved)
    except Exception as exc:
        if not isinstance(exc, ValueError):
            raise
        flash(str(exc))
        return redirect(url_for('live_batches.index'))
    return redirect(url_for('live_batches.detail', batch_id=batch_id))


@live_batches.route('/live-entry/<int:batch_id>')
def detail(batch_id):
    batch = get_batch(batch_id)
    book, _, layout = open_workbook(batch['workbook'])
    book.close()
    with db() as con:
        rows = con.execute('SELECT row_number,status,updated_at,form_id,error,attempted_submit FROM live_batch_rows WHERE batch_id=? ORDER BY row_number', (batch_id,)).fetchall()
    headers = json.loads(batch['headers'])
    definition = saved_mapping()
    auto, mapping_errors = resolve_headers(headers, definition)
    selected = json.loads(batch['header_mapping']) or auto
    return render_template('live_entry_detail.html', batch=batch, rows=rows, fields=FIELDS,
                           questionnaire=definition, headers=headers, mapping=selected, layout=layout,
                           auto=auto, mapping_errors=mapping_errors,
                           submission=submission_readiness(rows, batch_lock.locked()))


def validate_batch(batch_id, mapping, only_failed=False, only_pending=False):
    batch = get_batch(batch_id)
    if batch_lock.locked():
        raise ValueError('Wait for the current dry run to finish before changing mappings.')
    headers = json.loads(batch['headers'])
    if set(mapping) != set(FIELDS) or any(value not in headers or not value for value in mapping.values()) or len(set(mapping.values())) != len(FIELDS):
        raise ValueError('Map every field to a different existing spreadsheet column.')
    if any(normalize_header(value) in REPORT_COLUMNS for value in mapping.values()):
        raise ValueError('Report columns cannot be mapped to questionnaire fields.')
    if any(sum(normalize_header(h) == normalize_header(value) for h in headers if h) != 1 for value in mapping.values()):
        raise ValueError('Mapped spreadsheet headers must be unique after normalization.')
    book, sheet, _ = open_workbook(batch['workbook'])
    indices = {field: headers.index(value) + 1 for field, value in mapping.items()}
    try:
        definitions = {field['column']: field for field in saved_mapping()['fields']}
    except (ValueError, OSError, KeyError, json.JSONDecodeError):
        book.close()
        raise ValueError('Saved questionnaire mapping is unavailable or invalid.')
    with db() as con:
        rows = con.execute('SELECT row_number,status,form_id,attempted_submit FROM live_batch_rows WHERE batch_id=? ORDER BY row_number', (batch_id,)).fetchall()
        selective = only_failed or only_pending
        if not selective and any(row['status'] in ('SUBMITTING', 'SUBMITTED', 'UNKNOWN') or row['attempted_submit'] or row['form_id'] for row in rows):
            book.close()
            raise ValueError('A batch with submitted or uncertain rows cannot be remapped.')
        if selective and json.loads(batch['header_mapping']) != mapping:
            book.close()
            raise ValueError('Retry requires the saved column mapping.')
        con.execute('UPDATE live_batches SET header_mapping=? WHERE id=?', (json.dumps(mapping), batch_id))
        for row in rows:
            if only_failed and (row['status'] != 'FAILED' or row['attempted_submit'] or row['form_id']):
                continue
            if only_pending and (row['status'] != 'PENDING' or row['attempted_submit'] or row['form_id']):
                continue
            errors = []
            for field, col in indices.items():
                cell = sheet.cell(row['row_number'], col)
                value = cell.value
                if cell.data_type == 'f':
                    errors.append(f'{field}: formulas are not accepted')
                elif value is None or not str(value).strip():
                    errors.append(f'{field}: missing value')
                elif field in MULTI_FIELDS and str(value).strip().casefold() == 'none':
                    if definitions[field]['required']:
                        errors.append(f'{field}: at least one selection is required')
                elif field != 'Consent' and value is not None and str(value).strip() and definitions[field]['options']:
                    options = definitions[field]['options']
                    selections = [part.strip() for part in re.split(r'[,;\n]', str(value)) if part.strip()] if definitions[field]['type'] in {'checkbox', 'multiselect'} else [str(value).strip()]
                    if not selections or len(selections) != len(set(selections)) or any(sum(o['label'] == selection and o['value'] != '' for o in options) != 1 for selection in selections):
                        errors.append(f'{field}: option is missing, invalid, or ambiguous')
            consent = sheet.cell(row['row_number'], indices['Consent']).value
            if not affirmative_consent(consent) and consent is not None and str(consent).strip():
                errors.append('Consent: explicit affirmative value required')
            con.execute('''UPDATE live_batch_rows SET status=?,updated_at=?,error=?
                           WHERE batch_id=? AND row_number=? AND status=? AND attempted_submit=0
                           AND (form_id IS NULL OR form_id='')''',
                        ('FAILED' if errors else 'PENDING', now(), '; '.join(errors), batch_id, row['row_number'], row['status']))
            con.commit()
    book.close()
    return True


@live_batches.route('/live-entry/<int:batch_id>/mapping', methods=['POST'])
def mapping(batch_id):
    values = {field: request.form.get(field, '') for field in FIELDS}
    try:
        validate_batch(batch_id, values)
        flash('Headers and values checked against the saved mapping. Website structure and browser validation remain unverified for this batch.')
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for('live_batches.detail', batch_id=batch_id))


@live_batches.route('/live-entry/<int:batch_id>/auto-map', methods=['POST'])
def auto_map(batch_id):
    batch = get_batch(batch_id)
    values, errors = resolve_headers(json.loads(batch['headers']), saved_mapping())
    if errors:
        for error in errors:
            flash(error)
    else:
        try:
            validate_batch(batch_id, values)
            flash('Headers automatically mapped and spreadsheet values checked. Website validation is still pending.')
        except ValueError as exc:
            flash(str(exc))
    return redirect(url_for('live_batches.detail', batch_id=batch_id))


def submission_readiness(rows, running=False):
    candidates = sum(row['status'] == 'VALIDATED' and not row['attempted_submit'] and not row['form_id'] for row in rows)
    reasons = []
    if not candidates:
        reasons.append('No validated rows without a previous submission attempt are available.')
    if running:
        reasons.append('A validation or submission job is in progress.')
    if any(row['status'] in {'PENDING', 'VALIDATING', 'SUBMITTING'} for row in rows):
        reasons.append('Wait for validation or submission of pending rows to finish.')
    if not reasons:
        reasons.append('Review the exact answers and authorize the live batch before submitting.')
    return {'enabled': bool(candidates and not running and not any(row['status'] in {'PENDING', 'VALIDATING', 'SUBMITTING'} for row in rows)), 'candidate_count': candidates, 'reason': ' '.join(reasons)}


def submission_rows(batch_id):
    with db() as con:
        return con.execute('SELECT row_number,status,attempted_submit,form_id FROM live_batch_rows WHERE batch_id=? ORDER BY row_number', (batch_id,)).fetchall()


def review_signature(batch, rows):
    eligible = [row['row_number'] for row in rows if row['status'] == 'VALIDATED' and not row['attempted_submit'] and not row['form_id']]
    return hashlib.sha256(batch['workbook'] + batch['header_mapping'].encode('utf-8') + json.dumps(eligible).encode('utf-8')).hexdigest(), eligible


@live_batches.route('/live-entry/<int:batch_id>/review')
def review(batch_id):
    batch = get_batch(batch_id)
    rows = submission_rows(batch_id)
    readiness = submission_readiness(rows, batch_lock.locked())
    if not readiness['enabled']:
        flash(readiness['reason'])
        return redirect(url_for('live_batches.detail', batch_id=batch_id))
    mapping = json.loads(batch['header_mapping'])
    if set(mapping) != set(FIELDS):
        abort(400, description='A complete column mapping is required.')
    book, sheet, layout = open_workbook(batch['workbook'])
    try:
        indexes = {field: layout['headers'].index(column) + 1 for field, column in mapping.items()}
        signature, eligible = review_signature(batch, rows)
        review_rows = []
        for number in eligible:
            answers = [sheet.cell(number, indexes[field]).value for field in FIELDS]
            if not affirmative_consent(answers[FIELDS.index('Consent')]):
                abort(400, description='Every eligible row must have explicit consent.')
            review_rows.append((number, answers))
    finally:
        book.close()
    token = secrets.token_urlsafe(24)
    session['live_submission_review'] = {'batch_id': batch_id, 'signature': signature, 'token': token}
    response = render_template('live_entry_review.html', batch=batch, rows=review_rows, fields=FIELDS, token=token)
    from flask import make_response
    response = make_response(response)
    response.headers['Cache-Control'] = 'no-store'
    return response


@live_batches.route('/live-entry/<int:batch_id>/submit', methods=['POST'])
def submit(batch_id):
    global active_batch_id
    batch = get_batch(batch_id)
    review_data = session.pop('live_submission_review', None)
    if not review_data or review_data.get('batch_id') != batch_id or not secrets.compare_digest(str(review_data.get('token', '')), request.form.get('review_token', '')) or request.form.get('authorized') != 'yes':
        abort(403, description='Review every answer and explicitly authorize the live submission first.')
    if not batch_lock.acquire(blocking=False):
        abort(409, description='Another browser job is already running.')
    try:
        batch = get_batch(batch_id)
        rows = submission_rows(batch_id)
        readiness = submission_readiness(rows, False)
        signature, eligible = review_signature(batch, rows)
        if not readiness['enabled'] or not secrets.compare_digest(signature, review_data['signature']):
            abort(409, description='The batch changed after review; review it again.')
        definition = saved_mapping()
        mapping = json.loads(batch['header_mapping'])
        active_batch_id = batch_id
        threading.Thread(target=_run_submission_batch, args=(batch_id, batch['workbook'], mapping, definition, eligible), kwargs={'operator_confirmed': True}, daemon=True).start()
    except BaseException:
        active_batch_id = None
        batch_lock.release()
        raise
    flash('Live submission started for the reviewed rows. A score page is recorded as Submitted; this does not prove a saved website record.')
    return redirect(url_for('live_batches.detail', batch_id=batch_id))


def _run_dry_batch(batch_id, data, header_mapping, definition, row_numbers=None):
    global active_batch_id
    book = None
    try:
        book, sheet, layout = open_workbook(data)
        headers = layout['headers']
        indexes = {field: headers.index(column) + 1 for field, column in header_mapping.items()}
        with db() as con:
            rows = con.execute("SELECT row_number FROM live_batch_rows WHERE batch_id=? AND status='PENDING' AND attempted_submit=0 AND (form_id IS NULL OR form_id='') ORDER BY row_number", (batch_id,)).fetchall()
        for row in rows:
            number = row['row_number']
            if row_numbers is not None and number not in row_numbers:
                continue
            with db() as con:
                changed = con.execute("UPDATE live_batch_rows SET status='VALIDATING',updated_at=?,error='' WHERE batch_id=? AND row_number=? AND status='PENDING' AND attempted_submit=0 AND (form_id IS NULL OR form_id='')", (now(), batch_id, number)).rowcount
            if not changed:
                continue
            try:
                answers = {field: sheet.cell(number, col).value for field, col in indexes.items()}
                browser_worker.call('dry_run_questionnaire', definition, answers)
                status, error = 'VALIDATED', ''
            except Exception as exc:
                status = 'FAILED'
                error = f'Unexpected row error ({type(exc).__name__}); check the browser and mapping.'
                if isinstance(exc, DryRunFailure):
                    error = str(exc)
                if isinstance(exc, ValueError) and str(exc) in {'Questionnaire structure changed.', 'Unexpected questionnaire URL.', 'Explicit consent is required.'}:
                    error = str(exc)
                elif isinstance(exc, ValueError):
                    labels = [field['label'] for field in definition['fields']]
                    if str(exc).startswith('Field validation failed: '):
                        names = str(exc)[len('Field validation failed: '):].split(', ')
                        if names and all(name in labels for name in names):
                            error = str(exc)
                    else:
                        match = next((label for label in labels if str(exc).startswith(label + ':')), None)
                        if match:
                            error = f'{match}: answer could not be mapped safely.'
            with db() as con:
                con.execute('UPDATE live_batch_rows SET status=?,updated_at=?,error=? WHERE batch_id=? AND row_number=? AND status=?', (status, now(), error, batch_id, number, 'VALIDATING'))
    finally:
        if book:
            book.close()
        active_batch_id = None
        batch_lock.release()


def _claim_submission(batch_id, row_number, answers):
    """Atomically record an attempt before any browser submission can start."""
    if not affirmative_consent(answers.get('Consent')):
        with db() as con:
            con.execute("UPDATE live_batch_rows SET status='FAILED',error=?,updated_at=? WHERE batch_id=? AND row_number=? AND status='VALIDATED' AND attempted_submit=0", ('Consent: explicit affirmative value required.', now(), batch_id, row_number))
        return False
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT status,attempted_submit,form_id FROM live_batch_rows WHERE batch_id=? AND row_number=?', (batch_id, row_number)).fetchone()
        if not row or row['status'] != 'VALIDATED' or row['attempted_submit'] or row['form_id']:
            return False
        digest = secrets.token_hex(32)  # Opaque attempt ID; no cross-batch matching.
        timestamp = now()
        con.execute('INSERT INTO live_submission_attempts (batch_id,row_number,entry_digest,state,created_at,updated_at) VALUES (?,?,?,?,?,?)', (batch_id, row_number, digest, 'SUBMITTING', timestamp, timestamp))
        con.execute("UPDATE live_batch_rows SET status='SUBMITTING',attempted_submit=1,updated_at=?,error='' WHERE batch_id=? AND row_number=?", (timestamp, batch_id, row_number))
        return True


def _record_submission_result(batch_id, row_number, result):
    """Accept confirmation only from explicitly verified site-specific sources."""
    source = result.get('evidence_source')
    confirmed = result.get('confirmation_verified') is True and source in VERIFIED_CONFIRMATION_SOURCES
    form_id = result.get('form_id') if confirmed else None
    if form_id is not None and (not isinstance(form_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', form_id)):
        confirmed, form_id = False, None
    status = 'SUBMITTED' if confirmed else 'UNKNOWN'
    reason = result.get('uncertainty_reason')
    error = '' if confirmed else (reason if isinstance(reason, str) and len(reason) <= 500 else 'Submission outcome is unverified; reconcile before retrying.')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        changed = con.execute("UPDATE live_batch_rows SET status=?,form_id=?,error=?,updated_at=? WHERE batch_id=? AND row_number=? AND status='SUBMITTING'", (status, form_id, error, now(), batch_id, row_number)).rowcount
        if changed:
            con.execute("UPDATE live_submission_attempts SET state=?,updated_at=?,evidence=?,website_form_id=? WHERE batch_id=? AND row_number=? AND state='SUBMITTING'", (status, now(), source if confirmed else '', form_id, batch_id, row_number))
    return status


def _run_submission_batch(batch_id, data, header_mapping, definition, row_numbers, *, operator_confirmed=False):
    """Submit only reviewed, still-eligible rows; persist each outcome."""
    global active_batch_id
    book = None
    try:
        if not operator_confirmed or not VERIFIED_CONFIRMATION_SOURCES:
            return
        book, sheet, layout = open_workbook(data)
        indexes = {field: layout['headers'].index(column) + 1 for field, column in header_mapping.items()}
        for number in sorted(row_numbers):
            try:
                answers = {field: sheet.cell(number, col).value for field, col in indexes.items()}
                if not _claim_submission(batch_id, number, answers):
                    continue
                global_failure = False
                try:
                    result = browser_worker.call('submit_live_questionnaire', definition, answers, authorized=True)
                    if not isinstance(result, dict):
                        result = {}
                except Exception as exc:
                    # Once an attempt is recorded, an exception cannot prove that
                    # the remote site did not save the entry.
                    result = {'uncertainty_reason': (str(exc) if isinstance(exc, DryRunFailure) else f'Browser error ({type(exc).__name__}); outcome unverified.')}
                    global_failure = isinstance(exc, DryRunFailure) and exc.systemic
                _record_submission_result(batch_id, number, result)
                if global_failure:
                    break
            except sqlite3.Error:
                # A persistence failure makes further processing unsafe.
                break
            except Exception:
                # Preserve existing row state; do not guess whether a click occurred.
                with db() as con:
                    con.execute("UPDATE live_batch_rows SET status='UNKNOWN',error=?,updated_at=? WHERE batch_id=? AND row_number=? AND status='SUBMITTING'", ('Submission outcome is unverified; reconcile before retrying.', now(), batch_id, number))
                    con.execute("UPDATE live_submission_attempts SET state='UNKNOWN',updated_at=? WHERE batch_id=? AND row_number=? AND state='SUBMITTING'", (now(), batch_id, number))
                    con.execute("UPDATE live_batch_rows SET status='FAILED',error=?,updated_at=? WHERE batch_id=? AND row_number=? AND status='VALIDATED' AND attempted_submit=0", ('Could not prepare the row for submission.', now(), batch_id, number))
    finally:
        if book:
            book.close()
        active_batch_id = None
        batch_lock.release()


def start_dry_run(batch, headers, row_numbers=None):
    global active_batch_id
    try:
        definition = saved_mapping()
    except (ValueError, OSError, KeyError, json.JSONDecodeError):
        raise ValueError('Saved questionnaire mapping is unavailable or invalid.')
    if not batch_lock.acquire(blocking=False):
        raise ValueError('A dry run is already in progress.')
    try:
        active_batch_id = batch['id']
        threading.Thread(target=_run_dry_batch, args=(batch['id'], batch['workbook'], headers, definition, row_numbers), daemon=True).start()
    except Exception:
        active_batch_id = None
        batch_lock.release()
        raise


@live_batches.route('/live-entry/<int:batch_id>/dry-run', methods=['POST'])
def dry_run(batch_id):
    batch = get_batch(batch_id)
    headers = json.loads(batch['header_mapping'])
    if set(headers) != set(FIELDS):
        flash('Map and check the spreadsheet first.')
        return redirect(url_for('live_batches.detail', batch_id=batch_id))
    try:
        validate_batch(batch_id, headers, only_pending=True)
        start_dry_run(batch, headers)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for('live_batches.detail', batch_id=batch_id))
    flash('Dry run started. No final submission will be made.')
    return redirect(url_for('live_batches.detail', batch_id=batch_id))


@live_batches.route('/live-entry/<int:batch_id>/retry-failed', methods=['POST'])
def retry_failed(batch_id):
    batch = get_batch(batch_id)
    headers = json.loads(batch['header_mapping'])
    with db() as con:
        eligible = {row['row_number'] for row in con.execute("SELECT row_number FROM live_batch_rows WHERE batch_id=? AND status='FAILED' AND attempted_submit=0 AND (form_id IS NULL OR form_id='')", (batch_id,))}
    if not eligible:
        flash('No failed rows are eligible for a safe retry.')
        return redirect(url_for('live_batches.detail', batch_id=batch_id))
    try:
        validate_batch(batch_id, headers, only_failed=True)
        start_dry_run(batch, headers, eligible)
        flash('Eligible failed rows are being revalidated; no final submissions will be made.')
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for('live_batches.detail', batch_id=batch_id))


@live_batches.route('/live-entry/<int:batch_id>/progress')
def progress(batch_id):
    get_batch(batch_id)
    with db() as con:
        rows = con.execute('SELECT row_number,status,updated_at,form_id,error,attempted_submit FROM live_batch_rows WHERE batch_id=? ORDER BY row_number', (batch_id,)).fetchall()
    items = [dict(row) for row in rows]
    counts = {status: sum(row['status'] == status for row in items)
              for status in ('PENDING', 'VALIDATING', 'VALIDATED', 'FAILED', 'SUBMITTING', 'SUBMITTED', 'UNKNOWN')}
    running = active_batch_id == batch_id and batch_lock.locked()
    readiness = submission_readiness(items, batch_lock.locked())
    response = jsonify(rows=items, counts=counts, total=len(items), submission=readiness,
                       processed=sum(counts[s] for s in ('VALIDATED', 'FAILED', 'SUBMITTED', 'UNKNOWN')),
                       current_row=next((row['row_number'] for row in items if row['status'] in ('VALIDATING', 'SUBMITTING')), None),
                       running=running)
    response.headers['Cache-Control'] = 'no-store'
    return response


@live_batches.route('/live-entry/<int:batch_id>/export.xlsx')
def export(batch_id):
    batch = get_batch(batch_id)
    book, _, layout = open_workbook(batch['workbook'])
    sheet = book[batch['sheet_name']]
    report_cols = {}
    for index, header in enumerate(layout['headers'], 1):
        key = normalize_header(header)
        if key in REPORT_COLUMNS:
            if key in report_cols:
                book.close()
                abort(400, description='Duplicate report column headers must be resolved before export.')
            report_cols[key] = index
    next_col = layout['last_column'] + 1
    for key in ('form id', 'status', 'failure reason'):
        if key not in report_cols:
            report_cols[key] = next_col
            next_col += 1
    if max(report_cols.values()) > 16384:
        book.close()
        abort(400, description='No room for report columns after the last populated Excel column.')
    form_col, status_col, error_col = report_cols['form id'], report_cols['status'], report_cols['failure reason']
    with db() as con:
        rows = con.execute('SELECT row_number,status,form_id,error FROM live_batch_rows WHERE batch_id=?', (batch_id,)).fetchall()
    for row in rows:
        form_id = row['form_id'] if row['status'] == 'SUBMITTED' else None
        for col, value in ((form_col, form_id), (status_col, row['status']), (error_col, row['error'] or None)):
            current = sheet.cell(row['row_number'], col)
            if has_value(current) and current.value != value:
                book.close()
                abort(400, description='An existing report column contains data that would be overwritten.')
    sheet.cell(layout['header_row'], form_col, 'Form ID')
    sheet.cell(layout['header_row'], status_col, 'Status')
    sheet.cell(layout['header_row'], error_col, 'Failure reason')
    for row in rows:
        if row['status'] == 'SUBMITTED' and row['form_id']:
            sheet.cell(row['row_number'], form_col, row['form_id'])
        sheet.cell(row['row_number'], status_col, row['status'])
        if row['error']:
            sheet.cell(row['row_number'], error_col, row['error'])
    output = BytesIO()
    book.save(output)
    book.close()
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f'live-entry-{batch_id}-report.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
