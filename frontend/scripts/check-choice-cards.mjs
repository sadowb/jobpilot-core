// Exercise the actual batch renderer and requests, without a server or browser.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');
let values = {};
let skipped = {};
let stateIndex = 0;
let actions = [];
const requests = [];
const fakeRequire = (name) => {
  if (name === 'react') return { ...React, useState: () => [stateIndex++ % 2 ? skipped : values, () => {}] };
  if (name === 'react-router-dom') return { Link: ({ to, children, ...props }) => React.createElement('a', { href: to, ...props }, children) };
  if (name.endsWith('/api/api')) return { api: {
    saveJobAnswers: async (...args) => requests.push(['answers', ...args]),
    resumeJob: async (...args) => requests.push(['resume', ...args]),
  } };
  if (name.endsWith('/hooks/useAction')) return { useAction: (fn) => { actions.push(fn); return { mutate: () => {}, isPending: false }; } };
  if (name.endsWith('/utils/humanize')) return { fieldLabel: (key) => key, companyName: (name) => name };
  if (name.endsWith('/approvals/SubmitEvidence')) return { SubmitEvidence: () => null };
  if (name.endsWith('/components/ui')) return {
    Button: ({ children, loading: _loading, ...props }) => React.createElement('button', props, children),
    ErrorMessage: () => null, ui: {},
  };
  return require(name);
};
const source = readFileSync(new URL('../src/features/needs-you/Inbox.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
const module = { exports: {} };
new Function('require', 'module', 'exports', code)(fakeRequire, module, module.exports);
const { Questions } = module.exports;
const job = (fields, id = 'j1') => ({ id, role: 'Engineer', company: 'Fixture', missing: fields.map((f) => f.key), missingFields: fields });
const render = (fields, jobs = [job(fields)]) => {
  actions = []; stateIndex = 0;
  return renderToStaticMarkup(React.createElement(Questions, { jobs }));
};
const base = { key: '#school', label: 'School', type: 'custom-select', required: true, options: [] };
let html = render([{ ...base, options: ['A', 'B', '<script>untrusted</script>'] }]);
assert.match(html, /<select/);
assert.match(html, /&lt;script&gt;untrusted&lt;\/script&gt;/);
assert.doesNotMatch(html, /<script/);
html = render([base]);
assert.match(html, /will ask again/);
for (const type of ['file', 'secret', 'password']) {
  html = render([{ ...base, type }]);
  assert.match(html, /Answer controls are unavailable/);
  assert.match(html, /href="\/jobs\/j1"/);
  assert.doesNotMatch(html, /<input[^>]+type="text"/);
}
const rejected = { ...base, options: ['Other'], unmatchedValue: 'Example State University' };
const second = { ...base, key: '#why', label: 'Why?', type: 'textarea' };
html = render([rejected, second]);
assert.match(html, /Example State University.*not in this form&#x27;s list/);
assert.equal((html.match(/<form/g) ?? []).length, 1);
assert.equal((html.match(/Send answers/g) ?? []).length, 1);
values = { '#school': 'Other' }; skipped = { '#why': true };
render([rejected, second]);
await actions[0]();
const batch = [
  { question: '#school', answer: 'Other', scope: 'job:j1', skip: false },
  { question: '#why', answer: '', scope: 'job:j1', skip: true },
];
assert.deepEqual(requests.pop(), ['answers', 'j1', { answers: batch, resume: true }]);
await actions[1]();
assert.deepEqual(requests.splice(0), [
  ['answers', 'j1', { answers: batch, resume: false }], ['resume', 'j1', true],
]);
html = render([base], [job([base]), job([base], 'j2')]);
assert.equal((html.match(/<form/g) ?? []).length, 2);
console.log('Batch cards: choices, escaping, sensitive/file fields, job scope, skip, one send, try-anyway saves passed.');
