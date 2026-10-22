"""JavaScript injected into pages via `agent-browser eval`. Deterministic scans; no model involved."""

from __future__ import annotations

import json

DOM_HELPERS = r"""
const credential = e => {
  const label = [e.getAttribute('aria-label'), e.getAttribute('autocomplete'), e.name, e.id, e.placeholder, e.title, e.closest('label')?.textContent,
    ...[...(e.labels || [])].map(l => l.textContent),
    ...(e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent)].join(' ');
  const genericCode = /(?:^|[\s_-])(code|pin|token)(?:$|[\s_-])/i.test(label) && !/postal|zip|country|phone|dial|area|requisition|department|course|candidate|job.?code|token.?count/i.test(label);
  return e.type === 'password' || genericCode || /password|passwd|passphrase|username|user.?name|user.?id|account.?name|login|sign.?in|one.?time|otp|auth.?token|access.?token|api.?key|credential|passcode|two.?factor|mfa|2fa|captcha|secret|security.?code|verification.?code|\bpin\b/i.test(label)
    || (!!e.closest('form')?.querySelector('input[type=password]') && /email|user/i.test(label));
};
const selector = e => {
  if (e.id && document.querySelectorAll('#' + CSS.escape(e.id)).length === 1) return '#' + CSS.escape(e.id);
  if (e.name) { const s=e.tagName.toLowerCase() + '[name=' + JSON.stringify(e.name) + ']'; if(document.querySelectorAll(s).length===1) return s; }
  const parts=[];
  for(let n=e;n && n.nodeType===1;n=n.parentElement) {
    const tag=n.tagName.toLowerCase();
    const siblings=n.parentElement ? [...n.parentElement.children].filter(x=>x.tagName===n.tagName) : [n];
    parts.unshift(tag + ':nth-of-type(' + (siblings.indexOf(n)+1) + ')');
  }
  return parts.join(' > ');
};
const submitLike = e => {
  for(let n=e;n && n.nodeType===1;n=n.parentElement) {
    if (n.matches('button,input[type=submit],input[type=button],input[type=image],[role=button],a,[data-jp-submit]')) {
      const name=(n.getAttribute('aria-label') || n.value || n.textContent || n.title || '').trim();
      if (n.matches('[data-jp-submit],input[type=image]') || n.type==='submit' || /\bsubmit\b/i.test(name) || /^(apply(?: now)?|send application|finish|complete application|confirm application)$/i.test(name) || /\b(apply|send|finish|complete|confirm)\b.*\bapplication\b/i.test(name)) return true;
    }
  }
  return false;
};
"""


INVENTORY_JS = (
    "(() => {"
    + DOM_HELPERS
    + r"""
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none' && (r.width > 0 || r.height > 0 || e.type === 'file' || e.type === 'radio' || e.type === 'checkbox'); };
  const text = (n) => (n && n.textContent || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();
  const labelOf = (e) => {
    const aria = e.getAttribute('aria-label'); if (aria) return aria.trim();
    const lb = e.getAttribute('aria-labelledby'); if (lb) { const t = lb.split(/\s+/).map(id => text(document.getElementById(id))).join(' ').trim(); if (t) return t; }
    if (e.id) { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) return text(l); }
    const wrap = e.closest('label'); if (wrap) return text(wrap);
    const fs = e.closest('fieldset'); if (fs && fs.querySelector('legend') && (e.type === 'radio' || e.type === 'checkbox')) return text(fs.querySelector('legend'));
    return (e.placeholder || e.name || e.id || '').trim();
  };
  const sel = selector;
  const req = (e) => !!(e.required || e.getAttribute('aria-required') === 'true');
  const starred = (label) => /\*\s*$/.test(label || '');  // a visible "*" marks required fields that custom widgets don't flag
  const seen = new Set(); const out = [];
  for (const e of document.querySelectorAll('input,select,textarea,[contenteditable=true],[role=combobox],[role=checkbox],[role=radio]')) {
    const t = (e.type || e.tagName).toLowerCase();
    const role=e.getAttribute('role');
    if (['hidden','submit','reset','image'].includes(t) || (t==='button' && !['combobox','checkbox','radio'].includes(role)) || e.disabled) continue;
    if (credential(e)) { out.push({ key: sel(e) || 'password', label: labelOf(e) || 'Password', type: 'secret', required: req(e) || starred(labelOf(e)), selector: sel(e) }); continue; }
    if (!vis(e)) continue;
    if (t === 'radio') {
      const name = e.name; if (!name || seen.has('r:' + name)) continue; seen.add('r:' + name);
      const group = [...document.querySelectorAll('input[type=radio][name="' + name.replace(/"/g, '\\"') + '"]')];
      const options = group.map(r => text(document.querySelector('label[for="' + CSS.escape(r.id) + '"]') || r.closest('label')) || r.value);
      const optionSelectors = {}; group.forEach((r, i) => { optionSelectors[options[i]] = r.id ? '#' + CSS.escape(r.id) : 'input[type=radio][name="' + name + '"][value="' + r.value + '"]'; });
      const fs = e.closest('fieldset'); const lab = fs && fs.querySelector('legend') ? text(fs.querySelector('legend')) : labelOf(e);
      out.push({ key: 'radio:' + name, label: lab, type: 'radio', required: group.some(req) || starred(lab), options, optionSelectors });
      continue;
    }
    if (e.tagName === 'INPUT' && role !== 'combobox' && !labelOf(e) && !['checkbox','radio','file'].includes(t)) {
      // react-select style widgets render an extra unlabeled "required" input next to the real combobox; it is not a question
      let host = e.parentElement; let widget = false;
      for (let i = 0; host && i < 4 && !widget; i++, host = host.parentElement) widget = [...host.querySelectorAll('[role=combobox]')].some(c => c !== e);
      if (widget) continue;
    }
    const s = sel(e); if (!s || seen.has(s)) continue; seen.add(s);
    const f = { key: s, label: labelOf(e), required: req(e) || starred(labelOf(e)), selector: s };
    if (role==='combobox' && e.tagName!=='SELECT') f.type='custom-select';
    else if (['checkbox','radio'].includes(role) && e.tagName!=='INPUT') {f.type='custom-checkbox';}
    else if (e.tagName === 'SELECT') { f.type = 'select'; if(e.multiple) f.multiple=true; f.options = [...e.options].map(o => o.text.trim()).filter(Boolean); }
    else if (t === 'textarea' || e.isContentEditable) f.type = 'textarea';
    else if (t === 'checkbox') f.type = 'checkbox';
    else if (t === 'file') f.type = 'file';
    else if (['email','tel','date'].includes(t)) f.type = t;
    else f.type = 'text';
    out.push(f);
  }
  return JSON.stringify(out);
})()"""
)


def readback_js(selectors: list[str]) -> str:
    return (
        "(() => {"
        + DOM_HELPERS
        + " const sels = "
        + json.dumps(selectors)
        + r""";
  const out = {};
  for (const s of sels) {
    const e = document.querySelector(s); if (!e) { out[s] = { missing: true }; continue; }
    const t = (e.type || '').toLowerCase();
    if (credential(e)) { out[s] = { secret: true, filled: !!e.value }; continue; }
    if (e.getAttribute('role')==='combobox' && e.tagName!=='SELECT') {
      let selected=e.getAttribute('aria-valuetext') || (e.matches('input,textarea') ? e.value : '') || (e.textContent || '').replace(/\s+/g,' ').trim();
      if (!selected) { // react-select style: the chosen option is rendered next to the (empty) search input
        let host = e.parentElement;
        for (let i = 0; host && i < 5 && !selected; i++, host = host.parentElement) {
          const chosen = host.querySelector('[class*=single-value],[class*=singleValue],[class*=SingleValue]');
          if (chosen) selected = (chosen.textContent || '').replace(/\s+/g,' ').trim();
        }
      }
      // aria-expanded is often left "true" after a choice; only an actually visible popup means it is still open.
      const popup = (e.getAttribute('aria-controls') && document.getElementById(e.getAttribute('aria-controls'))) || (e.parentElement && e.parentElement.querySelector('[role=listbox]'));
      const open = e.getAttribute('aria-expanded')==='true' && (!popup || (!popup.hidden && popup.getClientRects().length > 0));
      const searching = e.matches('input,textarea') && e.getAttribute('aria-autocomplete')==='list' && !!e.value && !e.getAttribute('aria-valuetext');
      out[s]={value:selected, pending:open || searching || e.hasAttribute('data-placeholder')}; continue;
    }
    if (['checkbox','radio'].includes(e.getAttribute('role')) && e.tagName!=='INPUT') {out[s]={checked:e.getAttribute('aria-checked')==='true',value:e.getAttribute('aria-checked'),pending:!['true','false'].includes(e.getAttribute('aria-checked'))}; continue;}
    if (t === 'checkbox' || t === 'radio') out[s] = { checked: !!e.checked, value: e.value };
    else if (t === 'file') out[s] = { files: [...e.files].map(f => ({ name: f.name, size: f.size })) };
    else if (e.tagName === 'SELECT') out[s] = { value: e.multiple ? JSON.stringify([...e.selectedOptions].map(o=>o.text.trim())) : e.options[e.selectedIndex] ? e.options[e.selectedIndex].text.trim() : '' };
    else out[s] = { value: e.isContentEditable ? e.textContent : e.value };
  }
  return JSON.stringify(out);
})()"""
    )


PAGE_STATE_JS = r"""(() => {
  const errs = [...document.querySelectorAll('[aria-invalid="true"], [role="alert"], .error, .field-error, .invalid-feedback')]
    .map(e => (e.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean).slice(0, 12);
  return JSON.stringify({ url: location.href, host: location.hostname, title: document.title, errors: errs, text: (document.body.innerText || '').slice(0, 6000) });
})()"""

FIND_SUBMIT_JS = r"""(() => {
  document.querySelectorAll('[data-jp-submit]').forEach(e=>e.removeAttribute('data-jp-submit'));
  const controls=[...document.querySelectorAll('button,input[type=submit],input[type=button],[role=button]')].filter(e=>{
    const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    const name=(e.getAttribute('aria-label') || e.value || e.textContent || '').trim();
    return (e.type==='submit' || /^(submit(?: application)?|send application|finish|complete application|confirm application)$/i.test(name)) && !e.disabled && e.getAttribute('aria-disabled')!=='true'
      && r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'
      && /^(submit(?: application)?|apply(?: now)?|send application|finish|complete application|confirm application)$/i.test(name);
  });
  if(controls.length!==1) return '';
  controls[0].setAttribute('data-jp-submit','1');return '[data-jp-submit="1"]';
})()"""

# Only an exact, visible, unique non-submit button. A submit-type Next can send
# an application; it is never used for navigation without an approval.
FIND_NEXT_JS = r"""(() => {
  const visible = e => { const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; };
  const name = e => (e.getAttribute('aria-label') || e.value || e.textContent || '').trim();
  const next = [...document.querySelectorAll('button,input[type=button],input[type=submit],[role=button],a')]
    .filter(e => visible(e) && /\b(next|continue)\b/i.test(name(e)));
  document.querySelectorAll('[data-jp-next]').forEach(e=>e.removeAttribute('data-jp-next'));
  if (!next.length) return JSON.stringify({selector:null, blocked:false});
  if (next.length!==1 || !/^(next|continue)$/i.test(name(next[0]))
      || !next[0].matches('button[type=button],input[type=button]') || next[0].disabled
      || next[0].getAttribute('aria-disabled')==='true') return JSON.stringify({selector:null, blocked:true});
  next[0].setAttribute('data-jp-next','1');
  return JSON.stringify({selector:'[data-jp-next="1"]', blocked:false});
})()"""


SECRET_VALUES_JS = (
    "(() => {"
    + DOM_HELPERS
    + r"""
  return JSON.stringify([...document.querySelectorAll('input,textarea,[contenteditable=true]')]
    .filter(credential).map(e=>e.value || e.textContent || '').filter(Boolean));
})()"""
)


# Cookie / privacy banners block clicks on the form. Code (not the model) clears them: privacy-preserving choice first,
# plain dismissal second. Anything that reads like terms, an agreement or a legal attestation is left alone.
DISMISS_BANNER_JS = r"""(() => {
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const boxes = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true],[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[aria-label*=cookie i],[aria-label*=consent i],[aria-label*=privacy i]')].filter(visible);
  const reject = /^(reject|decline|deny|refuse|only (necessary|essential)|necessary( cookies)? only|essential( cookies)? only|use necessary)/i;
  const accept = /^(accept( all)?( cookies)?|allow( all)?|got it|ok(ay)?|i understand|agree|close|dismiss)$/i;
  for (const box of boxes) {
    const text = (box.innerText || '').slice(0, 600);
    if (!/cookie|privacy|consent|tracking/i.test(text) || /terms of (service|use)|i certify|i declare|background check|authorize/i.test(text)) continue;
    const buttons = [...box.querySelectorAll('button,[role=button],a[role=button],input[type=button],input[type=submit]')].filter(visible);
    const label = (b) => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
    const pick = buttons.find((b) => reject.test(label(b))) || buttons.find((b) => accept.test(label(b)));
    if (pick) { const name = label(pick); pick.click(); return JSON.stringify({ dismissed: true, choice: name.slice(0, 40) }); }
  }
  return JSON.stringify({ dismissed: false });
})()"""


OPTION_SELECTOR = "[role=option]"


def open_combobox_js(selector: str) -> str:
    """Open a custom (react-select style) dropdown and list its visible options. Leaves the menu open for pick_option_js."""
    return (
        "(async () => { const sel="
        + json.dumps(selector)
        + ";"
        + COMBO_HELPERS
        + r"""
  const el = document.querySelector(sel); if (!el) return JSON.stringify({ok:false, reason:'missing'});
  el.scrollIntoView({block:'center'});
  const options = () => comboOptions(el);
  const box = el.closest('[class*=control],[class*=Control]') || (el.matches('input,textarea') ? el.parentElement : el) || el;
  el.focus();
  if (el.getAttribute('aria-expanded') !== 'true' || !comboNodes(el).length) for (const type of ['mousedown','mouseup','click']) box.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, button:0}));
  let found = [];
  for (let i = 0; i < 12 && !found.length; i++) { await new Promise(r => setTimeout(r, 100)); found = options(); }
  if (!found.length) {
    el.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowDown', code:'ArrowDown', keyCode:40, bubbles:true}));
    for (let i = 0; i < 12 && !found.length; i++) { await new Promise(r => setTimeout(r, 100)); found = options(); }
  }
  return JSON.stringify({ok: found.length > 0, searchable: el.matches('input:not([readonly]),textarea:not([readonly])'), options: found});
})()"""
    )


def pick_option_js(text: str, selector: str) -> str:
    """Click the visible option whose text equals `text` (menu opened by open_combobox_js) and report what the
    dropdown then shows, so one browser call both chooses and reads back."""
    return (
        "(async () => { const want="
        + json.dumps(text)
        + ", sel="
        + json.dumps(selector)
        + ";"
        + COMBO_HELPERS
        + r"""
  const norm = t => (t || '').replace(/\s+/g,' ').trim();
  const opt = comboNodes(document.querySelector(sel)).find(o => norm(o.textContent) === want);
  if (!opt) return JSON.stringify({ok:false, reason:'option not found'});
  for (const type of ['mousedown','mouseup','click']) opt.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, button:0}));
  await new Promise(r => setTimeout(r, 200));
  const e = document.querySelector(sel);
  let shown = '';
  if (e) {
    shown = e.getAttribute('aria-valuetext') || (e.matches('input,textarea') ? e.value : '') || '';
    let host = e.parentElement;
    for (let i = 0; host && i < 5 && !shown; i++, host = host.parentElement) {
      const chosen = host.querySelector('[class*=single-value],[class*=singleValue],[class*=SingleValue]');
      if (chosen) shown = norm(chosen.textContent);
    }
  }
  const rb = JSON.parse("""
        + readback_js([selector])
        + r""");
  return JSON.stringify({ok:true, shown, pending:!!rb[sel]?.pending});
})()"""
    )


# Scoped to the control's popup; a different field's menu is never a source of choices.
COMBO_HELPERS = r"""
const comboNodes = el => {
  if (!el) return [];
  const id = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
  let popup = id && document.getElementById(id);
  if (!popup) {
    const menus = [...document.querySelectorAll('[role=listbox]')].filter(n => n.getClientRects().length > 0);
    if (menus.length !== 1) return [];
    popup = menus[0];
  }
  return [...popup.querySelectorAll('[role=option]')].filter(n => n.getClientRects().length > 0 && n.getAttribute('aria-disabled') !== 'true');
};
const comboOptions = el => [...new Set(comboNodes(el).map(o => (o.textContent || '').replace(/\s+/g,' ').trim()).filter(t => t && t.length <= 120))].slice(0,60);
const closeCombo = el => {
  el.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, bubbles:true}));
  el.blur();
};
"""
