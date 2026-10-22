// Unit checks for the pasted-link extractor (src/utils/links.ts): pure module, no browser or server.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const source = readFileSync(new URL('../src/utils/links.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const module = { exports: {} };
new Function('module', 'exports', code)(module, module.exports);
const { extractLinks, normalizeLink, decodeEntities, shortLink, chunk, sendInChunks, MAX_LINKS } = module.exports;
const links = (text, max) => extractLinks(text, max).links;

// separators: newline, space, comma, tab, semicolon
const a = 'https://jobs.lever.co/acme/1', b = 'https://boards.greenhouse.io/x/jobs/2?gh_jid=2', c = 'https://apply.workable.com/y/j/ABC';
assert.deepEqual(links(`${a}\n${b} ${c}`), [a, b, c]);
assert.deepEqual(links(`${a},${b}\t${c};`), [a, b, c]);
assert.deepEqual(links(`${a}\r\n\r\n${b}`), [a, b]);
// glued to text and to each other
assert.deepEqual(links(`apply here:${a} and also:${b}`), [a, b]);
assert.deepEqual(links(`${a}${b}`), [a, b]);
// markdown, angle brackets, quotes, trailing punctuation
assert.deepEqual(links(`[Acme job](${a}) and <${b}>. "${c}",`), [a, b, c]);
assert.deepEqual(links(`(see ${a}).`), [a]);
assert.deepEqual(links(`${a}!!! ${b}?`), [a, b]);
assert.deepEqual(links('https://en.example.org/wiki/Foo_(bar)'), ['https://en.example.org/wiki/Foo_(bar)']);
// html entities and query strings kept
assert.deepEqual(links('https://x.example.com/job?id=7&amp;gh_jid=9'), ['https://x.example.com/job?id=7&gh_jid=9']);
assert.equal(decodeEntities('&#x41;&#66;&quot;&bogus;&#0;'), 'AB"&bogus;&#0;');
// spreadsheet column / email copy
const column = `Company\tLink\nAcme\t${a}\nBeta\t${b}\n`;
assert.deepEqual(links(column), [a, b]);
assert.deepEqual(links(`Hi,\nPlease apply: ${a}\n\nThanks,\nSam\nsam@example.com`), [a]);
// dedupe: host case, fragment, trailing slash, tracking params; ATS ids survive
assert.deepEqual(links('HTTPS://Jobs.Lever.co/acme/1 https://jobs.lever.co/acme/1/ https://jobs.lever.co/acme/1#apply'), ['https://jobs.lever.co/acme/1']);
assert.equal(extractLinks('https://j.example.com/a?utm_source=x https://j.example.com/a?utm_medium=y&fbclid=1').duplicates, 1);
assert.deepEqual(links('https://j.example.com/a?utm_source=x&gh_jid=123&gclid=zz'), ['https://j.example.com/a?gh_jid=123']);
assert.deepEqual(links('https://j.example.com/a?ref=linkedin'), ['https://j.example.com/a']);
assert.deepEqual(links('https://j.example.com/a?ref=ab12cd'), ['https://j.example.com/a?ref=ab12cd']);
assert.deepEqual(links('https://j.example.com/a?gh_jid=1'), ['https://j.example.com/a?gh_jid=1']);
assert.notDeepEqual(links('https://j.example.com/a?gh_jid=1 https://j.example.com/a?gh_jid=2').length, 1);
assert.deepEqual(links('https://x.example.com/#/jobs/5'), ['https://x.example.com/#/jobs/5']); // client-side route kept
assert.deepEqual(links('https://j.example.com:443/a http://j.example.com:80/a'), ['https://j.example.com/a', 'http://j.example.com/a']);
// skipped: no scheme, other schemes, credentials, no dot host
let r = extractLinks('www.example.com/job ftp://files.example.com/x https://user:pw@ex.example.com/a http://localhost/x javascript:alert(1)');
assert.deepEqual(r.links, []);
assert.deepEqual(r.skipped.map((s) => s.reason).sort(), ['contains a login', 'missing https://', 'no website name', 'not an http(s) link', 'not an http(s) link']);
assert.deepEqual(extractLinks('nothing to see here, just words').skipped, []);
assert.deepEqual(extractLinks('').links, []);
// cap
const many = Array.from({ length: MAX_LINKS + 7 }, (_, i) => `https://j.example.com/job/${i}`).join('\n');
r = extractLinks(many);
assert.equal(r.links.length, MAX_LINKS);
assert.equal(r.overflow, 7);
assert.equal(extractLinks(many, 3).links.length, 3);
// helpers
assert.equal(shortLink('https://jobs.lever.co/acme/1/'), 'jobs.lever.co/acme/1');
assert.ok(shortLink(`https://a.example.com/${'x'.repeat(200)}`).length <= 64);
assert.deepEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
assert.deepEqual(chunk([], 50), []);
assert.deepEqual(normalizeLink('not a url'), { error: 'not a valid address' });
// chunked sending: order, progress, failure isolation
const ids = Array.from({ length: 120 }, (_, i) => `https://j.example.com/${i}`);
const seen = [];
const ok = await sendInChunks(ids, async (batch) => { seen.push(batch.length); return batch.map((u) => ({ u })); }, (u, m) => ({ u, m }), (d, t) => seen.push(`${d}/${t}`));
assert.deepEqual(seen, ['0/120', 50, '50/120', 50, '100/120', 20, '120/120']);
assert.deepEqual(ok.map((r) => r.u), ids);
let calls = 0;
const broke = await sendInChunks(ids, async (batch) => { if (++calls === 2) throw new Error('boom'); return batch.map((u) => ({ u })); }, (u, m) => ({ u, m }));
assert.equal(calls, 2);
assert.equal(broke.length, 120);
assert.ok(broke.slice(0, 50).every((r) => !r.m) && broke.slice(50).every((r) => r.m === 'boom'));
const short = await sendInChunks(['https://a.example.com', 'https://b.example.com'], async () => [{ u: 'x' }], (u, m) => ({ u, m }));
assert.equal(short[1].u, 'https://b.example.com');
console.log('Link extractor: separators, glued text, markdown, entities, tracking, dedupe, skipped, cap, helpers passed.');
