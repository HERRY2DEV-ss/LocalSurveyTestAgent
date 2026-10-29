import os
import ipaddress
import random
import re
import socket
import atexit
import hashlib
import json
from pathlib import Path
import queue
import threading
from datetime import datetime
from urllib.parse import urljoin, urlsplit, parse_qsl

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from consent import affirmative_consent


LIVE_TARGET_HOSTS = {'seeedemaseekhelp.com', 'www.seeedemaseekhelp.com'}
LIVE_TARGET_PATH = '/Ma_Vaishno_Devi/'
DEFAULT_LIVE_TARGET_URL = 'https://seeedemaseekhelp.com/Ma_Vaishno_Devi/'


class DryRunFailure(RuntimeError):
    def __init__(self, message, systemic=False):
        super().__init__(message)
        self.systemic = systemic


def questionnaire_fingerprint(fields, saved_fields):
    if len(fields) != len(saved_fields):
        raise ValueError('Questionnaire structure changed.')
    structure = [{'column': saved['column'], 'name': field['name'], 'id': field['id'],
                  'label': field['label'], 'type': field['type'], 'required': field['required'],
                  'options': [{'label': o['label'], 'value': o['value']} for o in field['options']]}
                 for saved, field in zip(saved_fields, fields)]
    encoded = json.dumps(structure, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()

FIELD_DETECTION_SCRIPT = r"""
() => {
  const secretPattern = /(password|passwd|token|secret|cookie|authorization|api.?key)/i;
  const all = [...document.querySelectorAll('input, textarea, select')];
  const usable = all.filter((el) => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    return type !== 'hidden' && type !== 'password' && type !== 'submit' && type !== 'reset' &&
      type !== 'button' && type !== 'image' &&
      !secretPattern.test(`${el.name || ''} ${el.id || ''} ${el.autocomplete || ''}`);
  });
  const groups = [];
  const grouped = new Map();
  for (const el of usable) {
    const type = (el.tagName === 'TEXTAREA') ? 'textarea' :
      (el.tagName === 'SELECT') ? (el.multiple ? 'multiselect' : 'select') :
      (el.type || 'text').toLowerCase();
    const isOptionGroup = ['radio', 'checkbox'].includes(type) && el.name;
    const key = isOptionGroup ? `${type}:${el.name}` : `single:${groups.length}`;
    if (isOptionGroup && grouped.has(key)) {
      grouped.get(key).elements.push(el);
    } else {
      const field = {type, name: el.name || '', elements: [el]};
      groups.push(field);
      if (isOptionGroup) grouped.set(key, field);
    }
  }
  const textOf = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const labelFor = (el) => {
    const labels = el.labels ? [...el.labels].map((x) => textOf(x.innerText || x.textContent)).filter(Boolean) : [];
    if (labels.length) return labels[0];
    const parentLabel = el.closest('label');
    if (parentLabel) return textOf(parentLabel.innerText || parentLabel.textContent);
    const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => document.getElementById(id)).filter(Boolean).map(x => textOf(x.innerText || x.textContent)).join(' ');
    if (labelledBy) return labelledBy;
    if (el.getAttribute('aria-label')) return textOf(el.getAttribute('aria-label'));
    if (['radio', 'checkbox'].includes(el.type) && el.nextElementSibling?.matches('label:not([for])'))
      return textOf(el.nextElementSibling.innerText || el.nextElementSibling.textContent);
    if (!['radio', 'checkbox'].includes(el.type) && el.previousElementSibling?.matches('label:not([for])'))
      return textOf(el.previousElementSibling.innerText || el.previousElementSibling.textContent);
    const fieldset = el.closest('fieldset');
    if (fieldset && fieldset.querySelector('legend')) return textOf(fieldset.querySelector('legend').innerText);
    return textOf(el.getAttribute('aria-label')) || el.name || el.id || 'Unlabelled field';
  };
  const nearbyGroupLabel = (el) => {
    let node = el;
    if (node.parentElement?.matches('ul, ol')) node = node.parentElement;
    for (let prev = node.previousElementSibling; prev; prev = prev.previousElementSibling) {
      if (prev.matches('label') && !prev.hasAttribute('for')) return textOf(prev.innerText || prev.textContent);
      if (prev.matches('br')) continue;
      break;
    }
    return '';
  };
  return {
    title: document.title,
    fields: groups.map((field, index) => {
      const first = field.elements[0];
      const key = `detected-${index}`;
      for (const el of field.elements) el.setAttribute('data-local-agent-field', key);
      const isOptionGroup = ['radio', 'checkbox'].includes(field.type);
      const options = isOptionGroup
        ? field.elements.map((el) => ({label: labelFor(el), value: el.value, selected: el.checked}))
        : (first.tagName === 'SELECT'
          ? [...first.options].map((o) => ({label: textOf(o.label || o.textContent), value: o.value, selected: o.selected}))
          : []);
      const rawLabel = labelFor(first);
      const group = isOptionGroup
        ? (first.closest('fieldset')?.querySelector('legend')?.innerText ||
           first.closest('[role="group"]')?.getAttribute('aria-label') ||
           first.getAttribute('aria-label') || nearbyGroupLabel(first) || (field.elements.length === 1 ? rawLabel : field.name) || rawLabel)
        : rawLabel;
      const required = field.elements.some((el) => el.required);
      const valid = field.elements.every((el) => el.checkValidity());
      let value = null;
      if (!['password', 'file'].includes(field.type)) {
        if (isOptionGroup) value = field.elements.filter((el) => el.checked).map((el) => el.value);
        else if (first.tagName === 'SELECT' && first.multiple) value = [...first.selectedOptions].map((o) => o.value);
        else if (first.type === 'checkbox') value = first.checked;
        else value = first.value ?? '';
      }
      return {
        key, label: textOf(group) || rawLabel, type: field.type, element: first.tagName.toLowerCase(),
        name: field.name, id: first.id || '', placeholder: first.placeholder || '',
        required, value, options,
        valid, validation_message: valid ? '' : (first.validationMessage || 'Field is invalid'),
        editable: field.elements.some((el) => !el.disabled && !el.readOnly),
        supported: !['file', 'image', 'range', 'color'].includes(field.type),
        error: ''
      };
    }),
    unsupported: [
      ...[...document.querySelectorAll('input[type="password"]')].map(() => ({
        role: 'protected-field', label: 'Password field excluded from inspection'
      })),
      ...[...document.querySelectorAll('input, textarea, select')]
        .filter((el) => secretPattern.test(`${el.name || ''} ${el.id || ''} ${el.autocomplete || ''}`))
        .map(() => ({role: 'protected-field', label: 'Secret-like field excluded from inspection'})),
      ...[...document.querySelectorAll('[contenteditable="true"], [role="combobox"], [role="listbox"], [role="checkbox"], [role="radio"]')]
      .filter((el) => !el.matches('input, select, textarea'))
      .map((el) => ({role: el.getAttribute('role') || 'contenteditable', label: textOf(el.innerText || el.getAttribute('aria-label')) || 'Custom widget'}))
    ]
  };
}
"""


def validate_target_url(value, allow_live=False):
    if value is None:
        raise ValueError('Enter a URL to open.')
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {'http', 'https'}:
        raise ValueError('Use an HTTP or HTTPS URL only.')
    host = (parsed.hostname or '').lower()
    if not host or parsed.username or parsed.password:
        raise ValueError('Provide a valid host URL without embedded credentials.')
    if any(re.search(r'password|passwd|token|secret|cookie|authorization|api.?key', key, re.I)
           for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValueError('Sensitive query parameters are not allowed.')
    if host in {'localhost', '127.0.0.1', '::1'}:
        if allow_live:
            return parsed._replace(fragment='').geturl()
        resolved = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
        if not resolved or any(
            not ipaddress.ip_address(address[4][0].split('%')[0]).is_loopback
            for address in resolved
        ):
            raise ValueError('Loopback host resolution failed.')
        return parsed._replace(fragment='').geturl()
    if host not in LIVE_TARGET_HOSTS and not allow_live:
        raise ValueError('Use an HTTP/HTTPS URL on localhost or a loopback IP address only.')
    if not allow_live:
        raise ValueError('Live target browsing is disabled in local-only mode.')
    if host not in LIVE_TARGET_HOSTS:
        raise ValueError('Only the authorized live survey host is supported.')
    if parsed.scheme != 'https' or parsed.port not in (None, 443):
        raise ValueError('The live survey requires HTTPS on the standard port.')
    if parsed.path != '/Ma_Vaishno_Devi' and not parsed.path.startswith(LIVE_TARGET_PATH):
        raise ValueError('Only the authorized live survey path is allowed.')
    return parsed._replace(fragment='').geturl()


def validate_local_url(value):
    try:
        return validate_target_url(value, allow_live=False)
    except ValueError:
        raise


class LocalBrowser:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.last_fields = []
        self.last_meta = {}
        self.last_submission_response_status = None
        self.last_detection_url = ''
        self.last_mutation_version = 0

    @property
    def is_open(self):
        return self.page is not None and not self.page.is_closed()

    def open(self, url, session_cookie=None, cookie_name='session', allow_live=False):
        safe_url = validate_target_url(url, allow_live=allow_live)
        self.stop()
        self.playwright = sync_playwright().start()
        headless = os.environ.get('LOCAL_TEST_HEADLESS', '').strip().lower() in {'1', 'true', 'yes'}
        try:
            launch_options = {'headless': headless}
            # Respect explicit browser-path configuration and standard installs.
            # The project may also contain the matching Chromium downloaded by setup.
            if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
                expected = Path(self.playwright.chromium.executable_path)
                if not expected.is_file() and len(expected.parts) >= 3:
                    candidate = Path(__file__).resolve().parent / '.playwright-browsers' / Path(*expected.parts[-3:])
                    if candidate.is_file():
                        launch_options['executable_path'] = str(candidate)
            self.browser = self.playwright.chromium.launch(**launch_options)
            self.context = self.browser.new_context()
            self.context.route('**/*', self._restrict_live_request if allow_live else self._restrict_request)
            if session_cookie:
                parsed = urlsplit(safe_url)
                self.context.add_cookies([{
                    'name': cookie_name,
                    'value': session_cookie,
                    'url': f'{parsed.scheme}://{parsed.netloc}',
                    'httpOnly': True,
                    'sameSite': 'Lax',
                }])
            self.page = self.context.new_page()
            self.page.on(
                'response',
                lambda response: setattr(self, 'last_submission_response_status', response.status)
                if response.request.is_navigation_request() else None,
            )
            self.page.goto(safe_url, wait_until='domcontentloaded', timeout=30000)
        except Exception:
            self.stop()
            raise
        return self.status()

    @staticmethod
    def _restrict_request(route):
        try:
            validate_target_url(route.request.url, allow_live=False)
        except ValueError:
            route.abort()
            return
        route.continue_()

    @staticmethod
    def _restrict_live_request(route):
        parsed = urlsplit(route.request.url)
        try:
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Invalid resource URL')
            if route.request.is_navigation_request():
                if parsed.hostname.lower() not in LIVE_TARGET_HOSTS:
                    raise ValueError('Navigation is restricted to the live survey')
                validate_target_url(route.request.url, allow_live=True)
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0].split('%')[0]).is_global for item in addresses):
                raise ValueError('Non-public network address')
        except (ValueError, OSError):
            route.abort()
            return
        route.continue_()

    def status(self):
        open_page = self.is_open
        mutation_version = (
            self.page.evaluate('window.__localAgentMutationVersion ?? -1')
            if open_page else -1
        )
        current_url = self.page.url if open_page else ''
        return {
            'open': open_page,
            'url': current_url,
            'title': self.page.title() if open_page else '',
            'fields_stale': open_page and (
                current_url != self.last_detection_url
                or mutation_version != self.last_mutation_version
            ),
        }

    def detect(self):
        if not self.is_open:
            raise RuntimeError('Open a local test form in the browser first.')
        result = self.page.evaluate(FIELD_DETECTION_SCRIPT)
        frames = [frame.url for frame in self.page.frames if frame != self.page.main_frame]
        self.last_fields = result['fields']
        self.last_meta = {
            'title': result.get('title', ''),
            'url': self.page.url,
            'inspection_time': datetime.now().astimezone().isoformat(timespec='seconds'),
            'frames': frames,
            'unsupported': result.get('unsupported', []),
        }
        self.page.evaluate("""() => {
          window.__localAgentMutationObserver?.disconnect();
          window.__localAgentMutationVersion = 0;
          window.__localAgentMutationObserver = new MutationObserver(() => {
            window.__localAgentMutationVersion += 1;
          });
          if (document.body) window.__localAgentMutationObserver.observe(document.body, {
            subtree: true, childList: true, attributes: true, characterData: true
          });
        }""")
        self.last_detection_url = self.page.url
        self.last_mutation_version = 0
        return {'fields': self.last_fields, **self.last_meta}

    def build_plan(self, rules, seed=''):
        if not self.last_fields:
            raise RuntimeError('Detect fields before configuring answers.')
        generator = random.Random(str(seed)) if seed else random.Random()
        plan = []
        for field in self.last_fields:
            rule = rules.get(field['key'], {'action': 'skip'})
            action = rule.get('action', 'skip')
            expected = None
            reason = 'Skipped by configuration'
            if action == 'manual':
                expected = None
                reason = 'Left for the operator to enter or select in the visible browser'
            elif action == 'random':
                if not field['options']:
                    raise ValueError(f"{field['label']}: random selection requires detected options.")
                option = generator.choice(field['options'])
                expected = option['value']
                if field['type'] in {'checkbox', 'multiselect'}:
                    expected = [expected]
                reason = (
                    f"Randomly selected from detected options using seed {seed!r}"
                    if seed else 'Randomly selected from detected options without a configured seed'
                )
            elif action == 'value':
                expected = rule.get('value')
                if field['options']:
                    if rule.get('match') == 'label':
                        labels = expected if isinstance(expected, list) else [expected]
                        matched = []
                        for label in labels:
                            options = [o for o in field['options'] if o['label'] == label]
                            if len(options) != 1:
                                raise ValueError(f"{field['label']}: option label {label!r} is missing or ambiguous.")
                            matched.append(options[0]['value'])
                        expected = matched if isinstance(expected, list) else matched[0]
                    allowed = {option['value'] for option in field['options']}
                    values = expected if isinstance(expected, list) else [expected]
                    if any(value not in allowed for value in values):
                        raise ValueError(f"{field['label']}: configured answer is not an available option.")
                    if any(sum(option['value'] == value for option in field['options']) != 1 for value in values):
                        raise ValueError(f"{field['label']}: option value is ambiguous.")
                    if any(sum(other['label'] == option['label'] for other in field['options']) != 1
                           for option in field['options'] if option['value'] in values):
                        raise ValueError(f"{field['label']}: option label is ambiguous.")
                    if field['type'] in {'checkbox', 'multiselect'} and not isinstance(expected, list):
                        expected = [expected]
                if expected in ('', None, []):
                    raise ValueError(f"{field['label']}: enter a value or skip this field.")
                reason = 'Manually configured value'
            elif action not in {'skip', 'manual'}:
                raise ValueError(f"{field['label']}: unsupported action {action!r}.")
            is_consent = 'consent' in f"{field['label']} {field['name']}".lower()
            if is_consent and action != 'manual':
                raise ValueError(f"{field['label']}: consent must be handled manually.")
            if field['required'] and (action == 'skip' or (action == 'value' and expected in ('', None, []))):
                raise ValueError(f"{field['label']}: required field cannot be skipped.")
            if not field['editable'] and action != 'skip':
                raise ValueError(f"{field['label']}: field is not editable.")
            if not field['supported'] and action != 'skip':
                raise ValueError(f"{field['label']}: this control type is unsupported.")
            plan.append({
                'key': field['key'], 'label': field['label'], 'type': field['type'],
                'action': action, 'expected': expected, 'reason': reason,
                'expected_labels': [o['label'] for o in field['options'] if o['value'] in (expected if isinstance(expected, list) else [expected])],
            })
        return plan

    def apply_and_validate(self, plan):
        if not self.is_open:
            raise RuntimeError('Browser is not open.')
        result = []
        for item in plan:
            field = next(field for field in self.last_fields if field['key'] == item['key'])
            locator = self.page.locator(f'[data-local-agent-field="{item["key"]}"]')
            try:
                if item['action'] == 'skip':
                    actual = self._actual(field, locator)
                    result.append({**item, 'actual': actual, 'result': 'SKIPPED', 'error': ''})
                    continue
                if item['action'] == 'manual':
                    actual = self._actual(field, locator)
                    valid = locator.evaluate_all('(els) => els.every((el) => el.checkValidity())')
                    result.append({
                        **item,
                        'actual': actual,
                        'result': 'VALIDATED' if valid else 'VALIDATION_FAILED',
                        'error': '' if valid else (field['validation_message'] or 'Complete this field manually in the browser.'),
                    })
                    continue
                expected = item['expected']
                if field['type'] == 'select':
                    locator.first.select_option(value=str(expected))
                elif field['type'] == 'multiselect':
                    locator.first.select_option(value=expected if isinstance(expected, list) else [str(expected)])
                elif field['type'] == 'radio':
                    choice = self.page.locator(
                        f'[data-local-agent-field="{item["key"]}"][value="{self._css_string(expected)}"]'
                    )
                    choice.check()
                elif field['type'] == 'checkbox':
                    choices = expected if isinstance(expected, list) else [expected]
                    for option in field['options']:
                        option_locator = self.page.locator(
                            f'[data-local-agent-field="{item["key"]}"][value="{self._css_string(option["value"])}"]'
                        )
                        if option['value'] in choices:
                            option_locator.check()
                        else:
                            option_locator.uncheck()
                elif field['type'] == 'file':
                    raise ValueError('File controls are not supported.')
                else:
                    locator.first.fill(str(expected))
                actual = self._actual(field, locator)
                passed = actual == expected or (
                    field['type'] == 'radio' and actual == expected
                )
                result.append({**item, 'actual': actual, 'result': 'VALIDATED' if passed else 'VALIDATION_FAILED',
                               'error': '' if passed else f'Expected {expected!r}; page has {actual!r}.',
                               'error_kind': '' if passed else 'read-back mismatch'})
            except (PlaywrightError, ValueError) as exc:
                reason = 'control timeout' if isinstance(exc, PlaywrightTimeoutError) else ('invalid control value' if isinstance(exc, ValueError) else 'control operation failed')
                result.append({**item, 'actual': None, 'result': 'VALIDATION_FAILED', 'error': str(exc), 'error_kind': reason})
        return result

    @staticmethod
    def _actual(field, locator):
        if field['type'] == 'radio':
            return locator.evaluate_all('(els) => els.find((el) => el.checked)?.value ?? null')
        if field['type'] == 'checkbox':
            return locator.evaluate_all('(els) => els.filter((el) => el.checked).map((el) => el.value)')
        if field['type'] == 'multiselect':
            return locator.first.evaluate('(el) => [...el.selectedOptions].map((option) => option.value)')
        if field['type'] == 'select':
            return locator.first.input_value()
        if field['type'] == 'file':
            return None
        return locator.first.input_value()

    @staticmethod
    def _css_string(value):
        return str(value).replace('\\', '\\\\').replace('"', '\\"')

    def submit_and_confirm(self):
        if not self.is_open:
            raise RuntimeError('Browser is not open.')
        self.last_submission_response_status = None
        form = self.page.locator('form').first
        if not form.evaluate('(form) => form.reportValidity()'):
            return {'status': 'SUBMISSION_FAILED', 'error': 'Browser form validation failed.'}
        try:
            with self.page.expect_navigation(wait_until='domcontentloaded', timeout=15000):
                form.evaluate('(form) => form.requestSubmit()')
        except PlaywrightTimeoutError:
            return {'status': 'SUBMISSION_UNVERIFIED', 'error': 'No page navigation or confirmation was observed.'}
        if self.last_submission_response_status is not None and not 200 <= self.last_submission_response_status < 300:
            return {
                'status': 'SUBMISSION_FAILED',
                'error': f'The local form returned HTTP {self.last_submission_response_status}.',
            }
        confirmed = (
            self.page.locator('h1').count() > 0
            and self.page.locator('h1').inner_text().strip() == 'Local test entry saved'
            and self.page.get_by_text('SUBMITTED_LOCAL', exact=True).count() > 0
            and self.page.get_by_text('Local database ID:', exact=False).count() > 0
        )
        if not confirmed:
            return {'status': 'SUBMISSION_UNVERIFIED', 'error': 'No recognized success-page evidence was found.'}
        record_link = self.page.locator('a[href*="/record/"]').first.get_attribute('href')
        record_id = self.page.locator('b').nth(1).inner_text().strip() if self.page.locator('b').count() > 1 else ''
        return {
            'status': 'SUBMITTED_CONFIRMED',
            'record_id': record_id,
            'record_link': urljoin(self.page.url, record_link) if record_link else '',
            'error': '',
        }

    def stop(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.last_fields = []
        self.last_meta = {}
        self.last_submission_response_status = None
        self.last_detection_url = ''
        self.last_mutation_version = 0

    def check_selector(self, selector):
        if not self.is_open:
            raise RuntimeError('Browser is not open.')
        self.page.locator(selector).check()

    def set_page_content(self, content):
        if not self.is_open:
            raise RuntimeError('Browser is not open.')
        self.page.set_content(content)

    def evaluate_page(self, script):
        if not self.is_open:
            raise RuntimeError('Browser is not open.')
        return self.page.evaluate(script)

    def navigate_page(self, url):
        if not self.is_open:
            raise RuntimeError('Browser is not open.')
        self.page.goto(validate_local_url(url), wait_until='domcontentloaded', timeout=20000)

    def submit_to_score_page(self, source_url, timeout_ms=15000):
        buttons = self.page.locator('form').first.locator('button[type="submit"], input[type="submit"]')
        if buttons.count() != 1:
            raise DryRunFailure('Final submission: submit control is missing or ambiguous.', systemic=True)
        score_pattern = re.compile(r'\bTotal\s+Score\s*[-:]\s*-?\d+(?:\.\d+)?\b', re.I)
        if score_pattern.search(self.page.locator('body').inner_text()):
            raise DryRunFailure('Final submission: a score was already present before submission.', systemic=True)
        with self.page.expect_response(lambda response: response.request.method == 'POST' and response.url.split('#', 1)[0] == source_url, timeout=timeout_ms) as response_info:
            buttons.first.click(timeout=timeout_ms)
        response = response_info.value
        if not 200 <= response.status < 400:
            return {'confirmation_verified': False, 'uncertainty_reason': f'Website returned HTTP {response.status}; outcome unverified.'}
        self.page.wait_for_function(r'''() => /\bTotal\s+Score\s*[-:]\s*-?\d+(?:\.\d+)?\b/i.test(document.body?.innerText || '')''', timeout=timeout_ms)
        current = urlsplit(self.page.url)
        expected = urlsplit(source_url)
        survey_prefix = expected.path.rsplit('/', 1)[0] + '/'
        if (current.scheme, current.netloc) != (expected.scheme, expected.netloc) or not current.path.startswith(survey_prefix):
            return {'confirmation_verified': False}
        if score_pattern.search(self.page.locator('body').inner_text()):
            return {'confirmation_verified': True, 'evidence_source': 'score-page'}
        return {'confirmation_verified': False}

    def dry_run_questionnaire(self, mapping, answers, *, final_submit=False):
        """Fill and validate one consented row; final submission is opt-in only."""
        if not affirmative_consent(answers.get('Consent')):
            raise ValueError('Explicit consent is required.')
        stage = 'Browser launch or page load'
        try:
            self.open(DEFAULT_LIVE_TARGET_URL, allow_live=True)
            stage = 'Initial language selection'
            self.page.locator('select[name="language"]').select_option(label=mapping['initial_steps']['language_label'])
            stage = 'Initial consent control'
            self.page.get_by_label(mapping['initial_steps']['consent_label'], exact=True).check()
            stage = 'Initial questionnaire navigation'
            self.page.get_by_role('button', name=mapping['initial_steps']['navigation_button'], exact=True).click()
            self.page.wait_for_load_state('domcontentloaded')
            if self.page.url != mapping['source_url']:
                raise ValueError('Unexpected questionnaire URL.')
            stage = 'Questionnaire field detection'
            fields = self.detect()['fields']
            if len(fields) != len(mapping['fields']):
                raise DryRunFailure(f'Questionnaire field detection: expected {len(mapping["fields"])} field groups; found {len(fields)}.', systemic=True)
            if questionnaire_fingerprint(fields, mapping['fields']) != mapping['fingerprint']:
                for saved, current in zip(mapping['fields'], fields):
                    changes = [key for key in ('name', 'id', 'label', 'type', 'required') if saved[key] != current[key]]
                    expected = [(o['label'], o['value']) for o in saved['options']]
                    actual = [(o['label'], o['value']) for o in current['options']]
                    if expected != actual:
                        changes.append('options')
                    if changes:
                        raise DryRunFailure(f'Questionnaire field detection: {saved["label"]}: changed {", ".join(changes)}.', systemic=True)
                raise DryRunFailure('Questionnaire field detection: structure fingerprint changed.', systemic=True)
            stage = 'Questionnaire answer mapping'
            rules = {}
            for saved, field in zip(mapping['fields'], fields):
                column = saved['column']
                if column == 'Consent':
                    locator = self.page.locator(f'[data-local-agent-field="{field["key"]}"]')
                    if locator.count() != 1:
                        raise ValueError('Consent control is ambiguous.')
                    locator.check()
                    rules[field['key']] = {'action': 'manual'}
                elif column in {'Clinical_Conditions', 'Past_Disorders'} and str(answers.get(column) or '').strip().casefold() == 'none':
                    rules[field['key']] = {'action': 'skip'}
                elif field['options']:
                    value = str(answers[column]).strip()
                    if field['type'] in {'checkbox', 'multiselect'}:
                        value = [part.strip() for part in re.split(r'[,;\n]', value) if part.strip()]
                    rules[field['key']] = {'action': 'value', 'match': 'label', 'value': value}
                else:
                    rules[field['key']] = {'action': 'value', 'value': str(answers[column]).strip()}
            plan = self.build_plan(rules)
            stage = 'Questionnaire field application'
            results = self.apply_and_validate(plan)
            failed = [r for r in results if r['result'] not in {'VALIDATED', 'SKIPPED'} or (r['result'] == 'SKIPPED' and r['actual'])]
            if failed:
                first = failed[0]
                label = first['label']
                reason = first.get('error_kind') or ('unexpected selected option' if first['result'] == 'SKIPPED' else 'required value missing or invalid')
                raise DryRunFailure(f'Questionnaire field application: {label}: {reason}.')
            stage = 'Website required-field validation'
            if not self.page.locator('form').first.evaluate('(form) => form.checkValidity()'):
                invalid = self.page.locator('form').first.locator(':invalid').evaluate_all('''els => els.map(e => ({
                    key: e.getAttribute('data-local-agent-field'),
                    missing: e.validity.valueMissing, type: e.validity.typeMismatch,
                    pattern: e.validity.patternMismatch, custom: e.validity.customError
                }))''')
                first = invalid[0] if invalid else {}
                label = next((field['label'] for field in fields if field['key'] == first.get('key')), 'unidentified field')
                reason = ('required value missing' if first.get('missing') else 'invalid value type' if first.get('type') else
                          'invalid value format' if first.get('pattern') else 'website validation rejected the field')
                raise DryRunFailure(f'Website validation: {label}: {reason}.')
            if final_submit:
                stage = 'Final questionnaire submission'
                return self.submit_to_score_page(mapping['source_url'])
            return {'status': 'VALIDATED'}
        except DryRunFailure:
            raise
        except Exception as exc:
            name = type(exc).__name__
            systemic = stage in {'Browser launch or page load', 'Initial language selection',
                                 'Initial consent control', 'Initial questionnaire navigation',
                                 'Questionnaire field detection'}
            if isinstance(exc, ValueError):
                message = str(exc)
                safe_messages = {'Questionnaire structure changed.', 'Unexpected questionnaire URL.',
                                 'Consent control is ambiguous.', 'Explicit consent is required.'}
                if message in safe_messages:
                    raise DryRunFailure(f'{stage}: {message}', systemic=True) from exc
                labels = [field['label'] for field in mapping['fields']]
                if message.startswith(('Field validation failed: ', 'Website validation failed for: ')):
                    prefix, _, values = message.partition(': ')
                    parts = values.split(', ')
                    if parts and all(part in labels for part in parts):
                        raise DryRunFailure(f'{stage}: {prefix}: {values}') from exc
                field_label = next((label for label in labels if message.startswith(label + ':')), None)
                if field_label:
                    spec = next(field for field in mapping['fields'] if field['label'] == field_label)
                    options = ', '.join(option['label'] for option in spec['options'] if option['value'])
                    suffix = f' Available options: {options}.' if options else ''
                    raise DryRunFailure(f'{stage}: {field_label}: option is missing, invalid, or ambiguous.{suffix}') from exc
            if 'Executable doesn\'t exist' in str(exc) and stage == 'Browser launch or page load':
                raise DryRunFailure('Browser launch: Playwright Chromium executable is missing. Run setup.bat or install the matching browser.', systemic=True) from exc
            reason = 'timed out or the required control was unavailable' if isinstance(exc, PlaywrightTimeoutError) else 'the browser operation failed'
            raise DryRunFailure(f'{stage}: {name}: {reason}.', systemic=systemic) from exc
        finally:
            self.stop()

    def submit_live_questionnaire(self, mapping, answers, *, authorized=False):
        if not authorized:
            raise ValueError('Explicit operator authorization is required.')
        return self.dry_run_questionnaire(mapping, answers, final_submit=True)


class BrowserWorker:
    def __init__(self):
        self._commands = queue.Queue()
        self._start_lock = threading.Lock()
        self._thread = None
        self._browser = None
        self._thread_id = None

    def _ensure_started(self):
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._serve, name='local-form-playwright', daemon=True,
                )
                self._thread.start()

    def _serve(self):
        self._thread_id = threading.get_ident()
        self._browser = LocalBrowser()
        while True:
            command = self._commands.get()
            if command is None:
                try:
                    self._browser.stop()
                finally:
                    self._browser = None
                    return
            operation, args, kwargs, completed, outcome = command
            try:
                outcome['value'] = getattr(self._browser, operation)(*args, **kwargs)
            except Exception as exc:
                outcome['error'] = exc
            finally:
                completed.set()

    def call(self, operation, *args, **kwargs):
        if threading.get_ident() == self._thread_id and self._browser is not None:
            return getattr(self._browser, operation)(*args, **kwargs)
        self._ensure_started()
        completed = threading.Event()
        outcome = {}
        self._commands.put((operation, args, kwargs, completed, outcome))
        completed.wait()
        if 'error' in outcome:
            raise outcome['error']
        return outcome.get('value')

    def shutdown(self):
        if self._thread is None or not self._thread.is_alive():
            return
        self._commands.put(None)
        self._thread.join(timeout=10)
