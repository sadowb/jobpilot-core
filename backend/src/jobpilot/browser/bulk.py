"""Server-authored DOM batch helpers. No clicking, submission, consent or credentials."""

from __future__ import annotations

import json
from typing import Any

from .js import DOM_HELPERS, INVENTORY_JS

OWNER_CONTEXT_JS = r"""
const ownerContext = e => {
  const legend=e?.closest('fieldset')?.querySelector('legend')?.textContent || '';
  const heading=e?.closest('section')?.querySelector('h2,h3,h4,[role=heading]')?.textContent || '';
  return !/\b(references?|referees?|referrer|emergency contact|next of kin|supervisor (contact|details|information)|manager (contact|details|information))\b/i.test(legend+' '+heading);
};
"""

BULK_SCAN_JS = (
    "(() => {"
    + OWNER_CONTEXT_JS
    + """
const fields = JSON.parse("""
    + INVENTORY_JS
    + """);
return fields.filter(f => f.type !== 'secret').slice(0, 200).map(f => {
  const e = f.selector && document.querySelector(f.selector);
  let occupied = !!(e && (e.value || e.textContent && e.isContentEditable));
  if (e && e.tagName === 'SELECT') occupied = !!(e.value && e.selectedOptions.length && !e.selectedOptions[0].disabled && !/^(select|choose|please select)\\b/i.test(e.selectedOptions[0].text.trim()));
  if (f.type === 'file') occupied = !!(e && e.files && e.files.length);
  return {...f, occupied, ownerContext:ownerContext(e)};
});
})()"""
)


def bulk_fill_js(items: list[dict[str, Any]]) -> str:
    return (
        "(() => {"
        + DOM_HELPERS
        + OWNER_CONTEXT_JS
        + "const items = "
        + json.dumps(items)
        + ";"
        + r"""
  const labelOf = e => {
    const clean = t => (t || '').replace(/\s+/g,' ').replace(/\*/g,'').trim();
    if (e.getAttribute('aria-label')) return e.getAttribute('aria-label').trim();
    if (e.getAttribute('aria-labelledby')) return e.getAttribute('aria-labelledby').split(/\s+/).map(id=>clean(document.getElementById(id)?.textContent)).join(' ').trim();
    if (e.id) {const l=document.querySelector('label[for="'+CSS.escape(e.id)+'"]'); if(l) return clean(l.textContent);}
    return clean(e.closest('label')?.textContent || e.placeholder || e.name || e.id);
  };
  const out=[];
  for (const item of items) {
    let status='skipped';
    try {
      const e=document.querySelector(item.selector);
      const visible=e && e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
      const plain=e && ((e.tagName==='INPUT' && ['text','email','tel','url','number','date',''].includes(e.type)) || e.tagName==='TEXTAREA' || e.tagName==='SELECT');
      if (!e || !e.isConnected || !visible || !plain || !ownerContext(e) || e.disabled || e.readOnly || credential(e) || submitLike(e) || e.getAttribute('role')==='combobox' && e.tagName!=='SELECT' || labelOf(e)!==item.label || /consent|agree|certif|signature|terms|privacy/i.test(item.label)) {
        out.push({selector:item.selector,status}); continue;
      }
      if (e.tagName==='SELECT') {
        const existing=e.selectedOptions[0];
        if (e.value && existing && !existing.disabled && !/^(select|choose|please select)\b/i.test(existing.text.trim())) {
          out.push({selector:item.selector,status:existing.text.trim()===item.value && e.validity.valid?'filled':'preserved'}); continue;
        }
        const matches=[...e.options].filter(o=>!o.disabled && o.text.trim()===item.value);
        if (e.multiple || matches.length!==1) {out.push({selector:item.selector,status});continue;}
        Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(e,matches[0].value);
      } else {
        if (e.value) {out.push({selector:item.selector,status:e.value===item.value && e.validity.valid?'filled':'preserved'});continue;}
        if (e.maxLength>0 && item.value.length>e.maxLength) {out.push({selector:item.selector,status});continue;}
        const proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto,'value').set.call(e,item.value);
      }
      e.dispatchEvent(new Event('input',{bubbles:true}));
      e.dispatchEvent(new Event('change',{bubbles:true}));
      status='written';
    } catch (_) {status='failed';}
    out.push({selector:item.selector,status});
  }
  // One read-back after all writes. React may rerender or reveal more controls.
  for (const row of out) {
    if(row.status!=='written') continue;
    const item=items.find(i=>i.selector===row.selector);
    const e=document.querySelector(row.selector);
    const value=e && e.tagName==='SELECT'?e.selectedOptions[0]?.text.trim():e?.value;
    row.status=e && !credential(e) && labelOf(e)===item.label && value===item.value && e.validity.valid?'filled':'failed';
  }
  return out;
})()"""
    )
