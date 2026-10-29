from datetime import datetime, timedelta
from contextlib import contextmanager
import atexit
from flask import Flask, flash, render_template, request, redirect, url_for, send_file, abort, session, jsonify
from pathlib import Path
from urllib.parse import urlsplit
import io
import json
import os
import random
import secrets
import socket
import sqlite3
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from dotenv import load_dotenv
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash
from browser_agent import BrowserWorker, DEFAULT_LIVE_TARGET_URL, validate_local_url, validate_target_url
from live_batches import live_batches, configure as configure_live_batches, init_tables as init_live_tables

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get('LOCAL_TEST_DB_PATH', BASE / 'test_submissions.sqlite3'))
load_dotenv(BASE / '.env')
app = Flask(__name__)
secret_key_file = BASE / '.app-secret-key'
app.config['SECRET_KEY'] = os.environ.get('APP_SECRET_KEY') or (
    secret_key_file.read_text(encoding='utf-8').strip()
    if secret_key_file.exists()
    else secrets.token_urlsafe(48)
)
if not secret_key_file.exists() and not os.environ.get('APP_SECRET_KEY'):
    secret_key_file.write_text(app.config['SECRET_KEY'], encoding='utf-8')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
)
csrf = CSRFProtect(app)
browser_worker = BrowserWorker()
atexit.register(browser_worker.shutdown)
active_browser_run_id = None

PROMOTERS = [f'Promoter-{i}' for i in range(1, 31)]
YESNO = ['Yes', 'No']

@app.before_request
def require_login():
    if request.endpoint not in {'login', 'static'} and not session.get('authenticated'):
        return redirect(url_for('login', next=request.path))
    return None

@contextmanager
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

configure_live_batches(db, browser_worker)
app.register_blueprint(live_batches)

def init_db():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_key TEXT UNIQUE,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            promoter TEXT NOT NULL,
            age_group TEXT NOT NULL,
            gender TEXT NOT NULL,
            swelling_both_legs TEXT NOT NULL,
            worse_evening TEXT NOT NULL,
            whole_body_swelling TEXT NOT NULL,
            face_swelling_morning TEXT NOT NULL,
            breathing_difficulty TEXT NOT NULL,
            breathing_lying_down TEXT NOT NULL,
            breathing_walking TEXT NOT NULL,
            conditions TEXT NOT NULL,
            past_disorders TEXT NOT NULL,
            status TEXT NOT NULL
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )''')
        con.execute('''INSERT OR IGNORE INTO app_settings (setting_key, setting_value)
            VALUES ('daily_limit', '6')''')
        con.execute('''CREATE TABLE IF NOT EXISTS browser_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            target_url TEXT NOT NULL,
            test_name TEXT NOT NULL,
            status TEXT NOT NULL,
            detected_fields TEXT NOT NULL DEFAULT '[]',
            field_results TEXT NOT NULL DEFAULT '[]',
            website_record_id TEXT,
            website_record_link TEXT,
            error TEXT,
            UNIQUE(run_date, target_url, test_name)
        )''')
        init_live_tables(con)

def current_daily_limit():
    with db() as con:
        row = con.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key='daily_limit'"
        ).fetchone()
    return max(1, min(6, int(row['setting_value']))) if row else 6

def payload():
    return request.get_json(silent=True) or request.form

def update_browser_run(run_id, **values):
    allowed = {'status', 'detected_fields', 'field_results', 'website_record_id',
               'website_record_link', 'error'}
    if not values or set(values) - allowed:
        raise ValueError('Invalid run update.')
    assignments = ', '.join(f'{key}=?' for key in values)
    with db() as con:
        con.execute(
            f'UPDATE browser_runs SET {assignments} WHERE id=?',
            list(values.values()) + [run_id],
        )

def make_answers(name):
    # Coherent synthetic combinations for LOCAL test data only; never sent to an external server.
    swelling = random.choice(YESNO)
    worse = 'Yes' if swelling == 'Yes' and random.random() < .75 else random.choice(YESNO)
    whole = random.choices(YESNO, weights=[1, 5])[0]
    face = random.choices(YESNO, weights=[1, 4])[0]
    breath = random.choices(YESNO, weights=[1, 5])[0]
    lying = 'Yes' if breath == 'Yes' and random.random() < .45 else random.choice(YESNO)
    walking = 'Yes' if breath == 'Yes' and random.random() < .55 else random.choice(YESNO)
    conds = random.sample(['Diabetes', 'High blood pressure', 'High cholesterol', 'Anaemic'], k=random.randint(0, 2))
    disorders = random.sample(['Liver disease', 'Kidney disease', 'Endocrine disease (e.g. thyroid disease)'], k=random.randint(0, 1))
    return {
        'name': name, 'promoter': random.choice(PROMOTERS),
        'age_group': random.choice(['Age ≤ 50 years', 'Age > 50 years']),
        'gender': random.choice(['Male', 'Female', 'Others']),
        'swelling_both_legs': swelling, 'worse_evening': worse,
        'whole_body_swelling': whole, 'face_swelling_morning': face,
        'breathing_difficulty': breath, 'breathing_lying_down': lying,
        'breathing_walking': walking, 'conditions': conds, 'past_disorders': disorders
    }

@app.route('/')
def index():
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM submissions WHERE status='SUBMITTED_LOCAL'").fetchone()[0]
    return render_template('index.html', names='', count=count, daily_limit=current_daily_limit())

@app.route('/automation')
def automation():
    with db() as con:
        today = datetime.now().date().isoformat()
        used = con.execute('SELECT COUNT(*) FROM browser_runs WHERE run_date=?', (today,)).fetchone()[0]
    return render_template(
        'automation.html',
        daily_limit=current_daily_limit(),
        daily_used=used,
        browser_status=browser_worker.call('status'),
        default_target_url=DEFAULT_LIVE_TARGET_URL,
    )

@app.route('/settings/daily-limit', methods=['POST'])
def set_daily_limit():
    try:
        limit = int(request.form.get('daily_limit', ''))
    except (TypeError, ValueError):
        flash('Daily limit must be a whole number from 1 to 6.')
        return redirect(url_for('automation'))
    if not 1 <= limit <= 6:
        flash('Daily limit must be between 1 and 6.')
        return redirect(url_for('automation'))
    with db() as con:
        con.execute(
            "UPDATE app_settings SET setting_value=? WHERE setting_key='daily_limit'",
            (str(limit),),
        )
    flash('Daily limit saved.')
    return redirect(url_for('automation'))

@app.route('/browser/open', methods=['POST'])
def browser_open():
    data = payload()
    url_value = data.get('url', '') if isinstance(data, dict) else ''
    try:
        try:
            validate_local_url(url_value)
            allow_live = False
        except ValueError:
            allow_live = True
        target_url = validate_target_url(url_value, allow_live=allow_live)
        status = browser_worker.call(
            'open',
            target_url,
            session_cookie=None if allow_live else request.cookies.get(app.config['SESSION_COOKIE_NAME']),
            cookie_name=app.config['SESSION_COOKIE_NAME'],
            allow_live=allow_live,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:
        app.logger.error('Could not open the browser (%s).', type(exc).__name__)
        return jsonify(error=str(exc)), 400
    session['browser_target_url'] = target_url
    return jsonify(**status)

@app.route('/browser/stop', methods=['POST'])
def browser_stop():
    browser_worker.call('stop')
    return jsonify(open=False, url='', title='')

@app.route('/browser/status')
def browser_status():
    return jsonify(**browser_worker.call('status'))

@app.route('/browser/detect', methods=['POST'])
def browser_detect():
    try:
        detected = browser_worker.call('detect')
    except Exception as exc:
        app.logger.error('Could not inspect local form fields (%s).', type(exc).__name__)
        return jsonify(error=str(exc)), 400
    session['detected_fields'] = detected['fields']
    session['detection_info'] = {
        'url': detected['url'],
        'inspection_time': detected['inspection_time'],
        'title': detected['title'],
        'frames': detected['frames'],
        'unsupported': detected['unsupported'],
    }
    return jsonify(detected)

@app.route('/browser/preview', methods=['POST'])
def browser_preview():
    global active_browser_run_id
    data = payload()
    name = str(data.get('name', '')).strip()
    if not name or len(name) > 100:
        return jsonify(error='Enter a test name of 1 to 100 characters.'), 400
    try:
        status = browser_worker.call('status')
        if not status['open'] or status['fields_stale']:
            raise ValueError('Detect and review fields on the current page before previewing.')
        target_url = validate_target_url(status['url'], allow_live=True)
        if not session.get('browser_target_url'):
            raise ValueError('Open the form in this dashboard session first.')
        opened = urlsplit(session['browser_target_url'])
        current = urlsplit(target_url)
        if (opened.scheme, opened.hostname, opened.port) != (current.scheme, current.hostname, current.port):
            raise ValueError('The current page does not match the form opened in this dashboard session.')
        rules_value = data.get('rules', {})
        rules = json.loads(rules_value) if isinstance(rules_value, str) else rules_value
        if not isinstance(rules, dict):
            raise ValueError('Field rules must be a JSON object.')
        plan = browser_worker.call('build_plan', rules, str(data.get('seed', '')))
        if target_url.startswith(('https://seeedemaseekhelp.com/', 'https://www.seeedemaseekhelp.com/')):
            if any(item['action'] == 'random' for item in plan):
                raise ValueError('Random answers are not allowed for the live survey.')
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return jsonify(error=str(exc)), 400
    today = datetime.now().date().isoformat()
    created = datetime.now().astimezone().isoformat(timespec='seconds')
    try:
        with db() as con:
            used = con.execute(
                'SELECT COUNT(*) FROM browser_runs WHERE run_date=?', (today,)
            ).fetchone()[0]
            prior_names = con.execute(
                'SELECT test_name FROM browser_runs WHERE run_date=? AND target_url=?',
                (today, target_url),
            ).fetchall()
            if any(row['test_name'].casefold() == name.casefold() for row in prior_names):
                return jsonify(error='A run with this name and target URL already exists today.'), 409
            row = con.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key='daily_limit'"
            ).fetchone()
            limit = max(1, min(6, int(row['setting_value']))) if row else 6
            if used >= limit:
                return jsonify(error=f'Daily limit reached ({limit} runs).'), 429
            cursor = con.execute(
                '''INSERT INTO browser_runs
                (run_date, created_at, target_url, test_name, status, detected_fields, field_results)
                VALUES (?, ?, ?, ?, 'PENDING', ?, ?)''',
                (today, created, target_url, name,
                 json.dumps({
                     'url': target_url,
                     'inspection_time': session.get('detection_info', {}).get('inspection_time', ''),
                     'frames': session.get('detection_info', {}).get('frames', []),
                     'unsupported': session.get('detection_info', {}).get('unsupported', []),
                     'fields': session.get('detected_fields', []),
                 }, ensure_ascii=False),
                 json.dumps(plan, ensure_ascii=False)),
            )
            run_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error='A run with this name and target URL already exists today.'), 409
    active_browser_run_id = run_id
    return jsonify(
        run_id=run_id,
        test_name=name,
        target_url=target_url,
        plan=plan,
        status='PENDING',
    )

@app.route('/browser/fill', methods=['POST'])
def browser_fill():
    if not active_browser_run_id:
        return jsonify(error='Create a run preview first.'), 400
    with db() as con:
        run = con.execute('SELECT * FROM browser_runs WHERE id=?', (active_browser_run_id,)).fetchone()
    if not run:
        return jsonify(error='Run record was not found.'), 404
    status_check = browser_worker.call('status')
    if not status_check['open'] or status_check['fields_stale'] or status_check['url'] != run['target_url']:
        return jsonify(error='The page changed after preview; detect fields and review a new plan.'), 409
    update_browser_run(active_browser_run_id, status='FILLED_NOT_SUBMITTED')
    try:
        plan = json.loads(run['field_results'])
        results = browser_worker.call('apply_and_validate', plan)
    except Exception as exc:
        app.logger.error('Could not fill or validate local form (%s).', type(exc).__name__)
        results = [
            {**item, 'actual': None, 'result': 'VALIDATION_FAILED', 'error': str(exc)}
            for item in json.loads(run['field_results'])
        ]
    passed = all(item['result'] in {'VALIDATED', 'SKIPPED'} for item in results)
    status = 'VALIDATED' if passed else 'VALIDATION_FAILED'
    update_browser_run(
        active_browser_run_id,
        status=status,
        field_results=json.dumps(results, ensure_ascii=False),
        error='' if passed else '; '.join(item['error'] for item in results if item['error']),
    )
    return jsonify(run_id=active_browser_run_id, status=status, results=results)

@app.route('/browser/submit', methods=['POST'])
def browser_submit():
    if not active_browser_run_id:
        return jsonify(error='Create and validate a run before submission.'), 400
    with db() as con:
        run = con.execute('SELECT * FROM browser_runs WHERE id=?', (active_browser_run_id,)).fetchone()
    if not run or run['status'] != 'VALIDATED':
        return jsonify(error='The run must pass validation before manual submission.'), 409
    try:
        validate_local_url(run['target_url'])
    except ValueError:
        return jsonify(error='Live submission is disabled. Review the answers and obtain explicit confirmation before any future submission.'), 403
    status_check = browser_worker.call('status')
    if not status_check['open'] or status_check['fields_stale']:
        message = 'Page navigation or dynamic changes occurred after validation. Detect fields and review a new run before submitting.'
        update_browser_run(
            active_browser_run_id,
            status='VALIDATION_FAILED',
            error=message,
        )
        return jsonify(error=message), 409
    update_browser_run(active_browser_run_id, status='SUBMISSION_ATTEMPTED', error='')
    try:
        result = browser_worker.call('submit_and_confirm')
    except Exception as exc:
        app.logger.error('Local form submission attempt failed (%s).', type(exc).__name__)
        result = {'status': 'SUBMISSION_FAILED', 'error': str(exc)}
    status = result['status']
    verified_id = result.get('record_id', '')
    verified_link = result.get('record_link', '')
    if status == 'SUBMITTED_CONFIRMED':
        try:
            verified_link = validate_local_url(verified_link)
            record_key = verified_link.rsplit('/', 1)[-1]
            with db() as con:
                record = con.execute(
                    'SELECT id, status FROM submissions WHERE submission_key=?',
                    (record_key,),
                ).fetchone()
            if not record or record['status'] != 'SUBMITTED_LOCAL' or str(record['id']) != verified_id:
                status = 'SUBMISSION_UNVERIFIED'
                result['error'] = 'Success page evidence did not match a saved local database record.'
        except sqlite3.Error as exc:
            app.logger.exception('Could not verify local record after submission.')
            status = 'SUBMISSION_UNVERIFIED'
            result['error'] = str(exc)
        except ValueError as exc:
            status = 'SUBMISSION_UNVERIFIED'
            result['error'] = str(exc)
    update_browser_run(
        active_browser_run_id,
        status=status,
        website_record_id=verified_id or None,
        website_record_link=verified_link or None,
        error=result.get('error', ''),
    )
    return jsonify(run_id=active_browser_run_id, **result)

@app.route('/history')
def history():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 20
    query = request.args.get('q', '').strip()
    test_name = request.args.get('test_name', '').strip()
    target_url = request.args.get('target_url', '').strip()
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    status = request.args.get('status', '').strip()
    sort = request.args.get('sort', 'newest')
    clauses, params = [], []
    if query:
        clauses.append('(test_name LIKE ? OR target_url LIKE ?)')
        params.extend([f'%{query}%', f'%{query}%'])
    if test_name:
        clauses.append('test_name LIKE ?')
        params.append(f'%{test_name}%')
    if target_url:
        clauses.append('target_url LIKE ?')
        params.append(f'%{target_url}%')
    if from_date:
        clauses.append('substr(created_at, 1, 10) >= ?')
        params.append(from_date)
    if to_date:
        clauses.append('substr(created_at, 1, 10) <= ?')
        params.append(to_date)
    if status:
        clauses.append('status=?')
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    order_by = {
        'newest': 'id DESC',
        'oldest': 'id ASC',
        'name': 'test_name COLLATE NOCASE ASC, id DESC',
        'status': 'status COLLATE NOCASE ASC, id DESC',
    }.get(sort, 'id DESC')
    with db() as con:
        total = con.execute(f'SELECT COUNT(*) FROM browser_runs{where}', params).fetchone()[0]
        rows = con.execute(
            f'SELECT * FROM browser_runs{where} ORDER BY {order_by} LIMIT ? OFFSET ?',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
    return render_template(
        'history.html', rows=rows, page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        query=query, test_name=test_name, target_url=target_url,
        from_date=from_date, to_date=to_date, status=status, sort=sort,
    )

@app.route('/browser-record/<int:run_id>')
def browser_record(run_id):
    with db() as con:
        row = con.execute('SELECT * FROM browser_runs WHERE id=?', (run_id,)).fetchone()
    if not row:
        abort(404)
    detection_data = json.loads(row['detected_fields'])
    if isinstance(detection_data, list):
        detection_data = {'url': row['target_url'], 'inspection_time': '', 'fields': detection_data}
    return render_template(
        'browser_record.html',
        row=row,
        fields=detection_data.get('fields', []),
        detection_info=detection_data,
        results=json.loads(row['field_results']),
    )

@app.route('/survey')
def survey():
    return render_template('survey.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    password_hash = os.environ.get('LOCAL_TEST_PASSWORD_HASH', '').strip()
    if not password_hash:
        abort(503, description='Set LOCAL_TEST_PASSWORD_HASH in your .env file. See README.md.')
    if request.method == 'POST':
        password = request.form.get('password', '')
        if check_password_hash(password_hash, password):
            session.clear()
            session.permanent = True
            session['authenticated'] = True
            return redirect(url_for('index'))
        flash('Incorrect password.')
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/submit', methods=['POST'])
def submit():
    form = request.form
    name = (form.get('name') or '').strip()
    if not name:
        abort(400, description='A test name is required.')
    if len(name) > 100:
        abort(400, description='Test names must be 100 characters or fewer.')
    choices = {
        'language': {'English', 'Hindi', 'Bengali', 'Marathi', 'Tamil', 'Telugu'},
        'promoter': set(PROMOTERS),
        'age_group': {'Age ≤ 50 years', 'Age > 50 years'},
        'gender': {'Male', 'Female', 'Others'},
        'swelling_both_legs': set(YESNO),
        'worse_evening': set(YESNO),
        'whole_body_swelling': set(YESNO),
        'face_swelling_morning': set(YESNO),
        'breathing_difficulty': set(YESNO),
        'breathing_lying_down': set(YESNO),
        'breathing_walking': set(YESNO),
    }
    for field, allowed in choices.items():
        if form.get(field) not in allowed:
            abort(400, description=f'Missing or invalid value for {field}.')
    if form.get('initial_consent') != 'yes' or form.get('final_consent') != 'yes':
        abort(400, description='Both local test consent checkboxes must be selected.')
    allowed_conditions = {'Diabetes', 'High blood pressure', 'High cholesterol', 'Anaemic'}
    allowed_disorders = {'Liver disease', 'Kidney disease', 'Endocrine disease (e.g. thyroid disease)'}
    if not set(form.getlist('conditions')).issubset(allowed_conditions):
        abort(400, description='An unsupported condition option was provided.')
    if not set(form.getlist('past_disorders')).issubset(allowed_disorders):
        abort(400, description='An unsupported disorder option was provided.')
    key = datetime.now().strftime('%Y%m%d%H%M%S') + '-' + secrets.token_urlsafe(6)
    created = datetime.now().astimezone().isoformat(timespec='seconds')
    vals = [name, form['promoter'], form['age_group'], form['gender'],
        form['swelling_both_legs'], form['worse_evening'], form['whole_body_swelling'],
        form['face_swelling_morning'], form['breathing_difficulty'], form['breathing_lying_down'],
        form['breathing_walking'], ', '.join(form.getlist('conditions')), ', '.join(form.getlist('past_disorders')), 'SUBMITTED_LOCAL']
    with db() as con:
        cur = con.execute('''INSERT INTO submissions (submission_key,created_at,name,promoter,age_group,gender,swelling_both_legs,worse_evening,whole_body_swelling,face_swelling_morning,breathing_difficulty,breathing_lying_down,breathing_walking,conditions,past_disorders,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [key, created] + vals)
        rowid = cur.lastrowid
    return render_template('success.html', sid=rowid, key=key, name=name, link=url_for('record', key=key, _external=True))

@app.route('/record/<key>')
def record(key):
    with db() as con:
        row = con.execute('SELECT * FROM submissions WHERE submission_key=?', (key,)).fetchone()
    if not row: return 'Local test record not found', 404
    return render_template('record.html', row=row)

@app.route('/run-tests', methods=['POST'])
def run_tests():
    names = [x.strip() for x in request.form.get('names','').splitlines() if x.strip()]
    if not names or len(names) > 100 or any(len(name) > 100 for name in names):
        flash('Enter between 1 and 100 names; each name must be 100 characters or fewer.')
        return redirect(url_for('index'))
    results = []
    for name in names:
        a = make_answers(name)
        key = datetime.now().strftime('%Y%m%d%H%M%S') + '-' + secrets.token_urlsafe(6)
        created = datetime.now().astimezone().isoformat(timespec='seconds')
        vals = [a['name'], a['promoter'], a['age_group'], a['gender'], a['swelling_both_legs'], a['worse_evening'], a['whole_body_swelling'], a['face_swelling_morning'], a['breathing_difficulty'], a['breathing_lying_down'], a['breathing_walking'], ', '.join(a['conditions']), ', '.join(a['past_disorders']), 'SUBMITTED_LOCAL']
        try:
            with db() as con:
                cur = con.execute('''INSERT INTO submissions (submission_key,created_at,name,promoter,age_group,gender,swelling_both_legs,worse_evening,whole_body_swelling,face_swelling_morning,breathing_difficulty,breathing_lying_down,breathing_walking,conditions,past_disorders,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [key, created] + vals)
                sid = cur.lastrowid
            results.append({'name': name, 'status':'SUBMITTED_LOCAL', 'id':sid, 'key':key, 'url':url_for('record', key=key, _external=True)})
        except sqlite3.Error as e:
            app.logger.exception('Local test record could not be saved.')
            results.append({'name': name, 'status':'FAILED_LOCAL', 'id':'', 'key':'', 'url':'', 'error':str(e)})
    return render_template('results.html', results=results)

def excel_safe(value):
    text = '' if value is None else str(value)
    if text.lstrip(' \t\r\n')[:1] in {'=', '+', '-', '@'}:
        return "'" + text
    return text

@app.route('/export.xlsx')
def export_xlsx():
    with db() as con:
        submissions = con.execute('SELECT * FROM submissions ORDER BY id').fetchall()
        browser_rows = con.execute('SELECT * FROM browser_runs ORDER BY id').fetchall()
    workbook = Workbook()
    records_ws = workbook.active
    records_ws.title = 'Test Records'
    records_ws.append([
        'Local ID', 'Test Name', 'Target URL', 'Timestamp', 'Status',
        'Website Record ID', 'Record Link', 'Summary',
    ])
    for row in browser_rows:
        record_link = row['website_record_link'] or ''
        records_ws.append([
            f"BROWSER-{row['id']}", excel_safe(row['test_name']), excel_safe(row['target_url']),
            excel_safe(row['created_at']), excel_safe(row['status']),
            excel_safe(row['website_record_id']), excel_safe(record_link),
            excel_safe(row['error'] or f"Browser test status: {row['status']}"),
        ])
        if record_link:
            try:
                records_ws.cell(records_ws.max_row, 7).hyperlink = validate_local_url(record_link)
            except ValueError:
                app.logger.warning('Excluded a non-loopback record link from Excel export.')
    for row in submissions:
        record_link = f"http://127.0.0.1:8765/record/{row['submission_key']}"
        records_ws.append([
            f"LOCAL-{row['id']}", excel_safe(row['name']), 'http://127.0.0.1:8765/survey',
            excel_safe(row['created_at']), excel_safe(row['status']), str(row['id']),
            record_link, 'Local sample form database write',
        ])
        records_ws.cell(records_ws.max_row, 7).hyperlink = record_link

    field_ws = workbook.create_sheet('Field Results')
    field_ws.append([
        'Test Reference', 'Field Label', 'Field Type', 'Expected Value',
        'Actual Value', 'Validation Result', 'Error Details',
    ])
    detected_ws = workbook.create_sheet('Detected Fields')
    detected_ws.append([
        'Target URL', 'Inspection Timestamp', 'Field Label', 'Field Type',
        'HTML Element', 'Field ID', 'Field Name', 'Required', 'Available Options',
    ])
    errors_ws = workbook.create_sheet('Errors')
    errors_ws.append([
        'Test Reference', 'Timestamp', 'Failed Operation',
        'Error Message', 'Diagnostic Details',
    ])
    for row in browser_rows:
        run_reference = f"BROWSER-{row['id']}"
        field_results = json.loads(row['field_results'] or '[]')
        detected_data = json.loads(row['detected_fields'] or '[]')
        if isinstance(detected_data, list):
            detected_data = {'url': row['target_url'], 'inspection_time': '', 'fields': detected_data}
        detected_fields = detected_data.get('fields', [])
        for field in detected_fields:
            options = ', '.join(
                f"{option.get('label', '')} ({option.get('value', '')})"
                for option in field.get('options', [])
            )
            detected_ws.append([
                excel_safe(detected_data.get('url') or row['target_url']),
                excel_safe(detected_data.get('inspection_time')),
                excel_safe(field.get('label')), excel_safe(field.get('type')),
                excel_safe(field.get('element')), excel_safe(field.get('id')),
                excel_safe(field.get('name')), 'Yes' if field.get('required') else 'No',
                excel_safe(options),
            ])
        for result in field_results:
            expected = result.get('expected')
            actual = result.get('actual')
            validation = result.get('result', row['status'])
            field_ws.append([
                run_reference, excel_safe(result.get('label')), excel_safe(result.get('type')),
                excel_safe(json.dumps(expected, ensure_ascii=False) if expected is not None else ''),
                excel_safe(json.dumps(actual, ensure_ascii=False) if actual is not None else ''),
                excel_safe(validation), excel_safe(result.get('error')),
            ])
            if result.get('error'):
                errors_ws.append([
                    run_reference, excel_safe(row['created_at']), 'Field validation',
                    excel_safe(result['error']),
                    excel_safe(json.dumps({
                        'field': result.get('label'),
                        'expected': expected,
                        'actual': actual,
                        'result': validation,
                    }, ensure_ascii=False)),
                ])
        if row['error']:
            errors_ws.append([
                run_reference, excel_safe(row['created_at']), excel_safe(row['status']),
                excel_safe(row['error']), excel_safe(row['target_url']),
            ])
        elif row['status'] == 'SUBMISSION_ATTEMPTED':
            errors_ws.append([
                run_reference, excel_safe(row['created_at']), 'Interrupted submission',
                'Submission was attempted but no confirmation result was persisted.',
                excel_safe(row['target_url']),
            ])

    for row in submissions:
        reference = f"LOCAL-{row['id']}"
        local_fields = (
            ('Name', 'name'), ('Promoter', 'promoter'), ('Age group', 'age_group'),
            ('Gender', 'gender'), ('Swelling in both legs', 'swelling_both_legs'),
            ('Worse in evening', 'worse_evening'), ('Whole body swelling', 'whole_body_swelling'),
            ('Face swelling in morning', 'face_swelling_morning'),
            ('Breathing difficulty', 'breathing_difficulty'),
            ('Breathing difficulty while lying down', 'breathing_lying_down'),
            ('Breathing difficulty while walking', 'breathing_walking'),
            ('Conditions', 'conditions'), ('Past disorders', 'past_disorders'),
        )
        for label, key in local_fields:
            value = excel_safe(row[key])
            field_ws.append([
                reference, label, 'Stored local answer', value, value,
                excel_safe(row['status']), '',
            ])

    for sheet in workbook.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='244062')
            cell.alignment = Alignment(wrap_text=True)
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in column)
            sheet.column_dimensions[get_column_letter(column[0].column)].width = min(max(max_len + 2, 12), 42)
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output, as_attachment=True, download_name='local_survey_test_results.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )

@app.route('/reset', methods=['POST'])
def reset():
    with db() as con:
        con.execute('DELETE FROM submissions')
        con.execute('DELETE FROM browser_runs')
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    print('\nLOCAL-ONLY Survey Test Agent')
    print('Open http://127.0.0.1:8765 in your browser.')
    print('This app stores data only in a local SQLite database and does not contact the live survey.\n')
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 8765))
    except OSError:
        raise SystemExit('Port 8765 is already in use. Close the other app or change the port in app.py.')
    app.run(host='127.0.0.1', port=8765, debug=False, threaded=True)
