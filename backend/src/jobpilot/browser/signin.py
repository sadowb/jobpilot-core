"""Page scripts that type a stored login into a sign-in or create-account form, and read the outcome (ADR 0031).

The password is an argument of Neo's `evaluate`, so it never reaches a model. Neo logs tool arguments (ADR 0028); that is a
known, accepted exposure. Every script refuses to run unless `location.origin` equals the origin the credential is stored
for, so a redirect or a wrong tab cannot receive another site's password. Scripts return metadata only, never the value. A sign-up fill also ticks the site's terms/privacy box (never marketing
boxes): the owner delegated that to the account step (NEO.md step 2); other unticked required boxes stop the flow.
"""

from __future__ import annotations

import json
from typing import Literal

Mode = Literal["signin", "signup"]

_COMMON = r"""
const vis = e => { const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none' && e.getClientRects().length > 0; };
const textOf = e => (e && e.textContent || '').replace(/\s+/g, ' ').trim();
const form = () => {
  const pw = [...document.querySelectorAll('input[type=password]')].filter(vis);
  if (!pw.length) return null;
  const root = pw[0].closest('form') || document.body;
  return { root, pw: [...root.querySelectorAll('input[type=password]')].filter(vis) };
};
"""

_FILL = (
    r"""(() => {
  const want = %(origin)s, mode = %(mode)s, user = %(user)s, secret = %(secret)s, allowConsent = %(consent)s;
  if (window.top !== window || location.origin !== want) return JSON.stringify({ok: false, reason: 'origin_mismatch'});
"""
    + _COMMON
    + r"""
  const f = form();
  if (!f) return JSON.stringify({ok: false, reason: 'no_password_field'});
  if (f.pw.length > (mode === 'signup' ? 2 : 1)) return JSON.stringify({ok: false, reason: 'unexpected_password_fields'});
  // Honeypots ("beecatcher", "website", tabindex -1) are bot traps: a value in one marks the sign-up as automated spam.
  const trap = e => /bee.?catcher|honey|trap|robot/i.test((e.getAttribute('data-automation-id') || '') + ' ' + e.name + ' ' + e.id)
    || e.name === 'website' || e.tabIndex === -1 || e.getAttribute('aria-hidden') === 'true';
  const login = [...f.root.querySelectorAll('input')].filter(vis).find(e =>
    !['password', 'hidden', 'checkbox', 'radio', 'submit', 'button', 'file'].includes(e.type) && !e.disabled && !e.readOnly && !trap(e));
  // a set-password page reached from an emailed link has password fields only: nothing to type as a username
  if (!login && mode !== 'signup') return JSON.stringify({ok: false, reason: 'no_username_field'});
  const set = (e, v) => {
    const proto = Object.getPrototypeOf(e);
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, v);
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
    return e.value === v;
  };
  const okUser = login ? set(login, user) : true;
  const okPw = f.pw.map(e => set(e, secret)).every(Boolean);
  // Creating the account needs the site's terms/privacy box. Marketing boxes are never ticked; unknown required boxes stay for the owner.
  const labelOf = e => ((e.labels && e.labels[0] && e.labels[0].textContent) || e.getAttribute('aria-label') || textOf(e.closest('label')) || textOf(e.parentElement && e.parentElement.parentElement) || '').slice(0, 300);
  const marketing = /newsletter|marketing|promotion|offers|updates|sms|text message|job alerts?|subscribe/i, terms = /agree|terms|privacy|acknowledge|consent|accept|conditions|policy|notice/i;
  let ticked = 0;
  if (mode === 'signup' && allowConsent) {
    for (const e of [...f.root.querySelectorAll('input[type=checkbox]')].filter(e => vis(e) && !e.checked && !e.disabled)) {
      const t = labelOf(e);
      if (terms.test(t + ' ' + (e.getAttribute('data-automation-id') || '')) && !marketing.test(t)) { e.click(); if (e.checked) ticked++; }
    }
  }
  const consent = [...f.root.querySelectorAll('input[type=checkbox]')].filter(e => vis(e) && !e.checked && (e.required || e.getAttribute('aria-required') === 'true' || (terms.test(labelOf(e)) && !marketing.test(labelOf(e))))).length;
  return JSON.stringify({ok: okUser && okPw, reason: okUser && okPw ? null : 'readback_mismatch', passwordFields: f.pw.length, consentPending: consent, consentTicked: ticked});
})()"""
)

_SUBMIT = (
    r"""(() => {
  const want = %(origin)s, mode = %(mode)s;
  if (window.top !== window || location.origin !== want) return JSON.stringify({ok: false, reason: 'origin_mismatch'});
"""
    + _COMMON
    + r"""
  const f = form();
  if (!f) return JSON.stringify({ok: false, reason: 'no_password_field'});
  const pattern = mode === 'signup' ? /create|register|sign.?up|join|continue|next|set.?password|save|reset|update|confirm|submit/i : /sign.?in|log.?in|continue|next|submit/i;
  const name = b => (b.getAttribute('aria-label') || b.value || textOf(b)).trim();
  const buttons = [...f.root.querySelectorAll('button, input[type=submit], [role=button]')].filter(b => vis(b) && !b.disabled);
  const button = buttons.find(b => (b.type === 'submit' || b.tagName === 'INPUT') && pattern.test(name(b))) || buttons.find(b => pattern.test(name(b)));
  if (!button) return JSON.stringify({ok: false, reason: 'no_submit_button'});
  // Workday-style buttons sit under a transparent overlay (data-automation-id=click_filter) that owns the click handler.
  const overlay = button.parentElement && button.parentElement.querySelector('[data-automation-id=click_filter]');
  const target = overlay || button;
  for (const type of ['mousedown', 'mouseup', 'click']) target.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
  return JSON.stringify({ok: true, clicked: name(button).slice(0, 40), overlay: !!overlay});
})()"""
)

_OUTCOME = (
    r"""(() => {
  const want = %(origin)s;
  if (location.origin !== want) return JSON.stringify({origin: false});
"""
    + _COMMON
    + r"""
  const errors = [...document.querySelectorAll('[role=alert], [aria-live=assertive], .error, .errors, .invalid-feedback, [class*=error], [data-automation-id*=error]')]
    .filter(vis).map(textOf).filter(Boolean).slice(0, 5);
  const invalid = document.querySelectorAll('input[aria-invalid=true]').length;
  const body = textOf(document.body).slice(0, 4000);
  return JSON.stringify({
    origin: true,
    passwordFieldVisible: !!form(),
    errors: errors.map(t => t.slice(0, 160)),
    invalid,
    verify: /verify your e-?mail|check your (e-?mail|inbox)|confirmation (link|code)|verification (link|code)|activate your account/i.test(body),
  });
})()"""
)


def _script(template: str, **values: object) -> str:
    return template % {key: json.dumps(value) for key, value in values.items()}


def fill_script(origin: str, mode: Mode, username: str, password: str, *, consent: bool = True) -> str:
    return _script(_FILL, origin=origin, mode=mode, user=username, secret=password, consent=consent)


def submit_script(origin: str, mode: Mode) -> str:
    return _script(_SUBMIT, origin=origin, mode=mode)


def outcome_script(origin: str) -> str:
    return _script(_OUTCOME, origin=origin)
