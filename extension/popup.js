import { BASE, isSendable, sendLink } from './shared.js';

const $ = (id) => document.getElementById(id);

function show(tone, glyph, title, detail = '', buttons = []) {
  $('icon').className = tone;
  $('icon').textContent = glyph;
  $('title').textContent = title;
  $('detail').textContent = detail;
  $('actions').replaceChildren(
    ...buttons.map(({ label, primary, onClick }) => {
      const b = document.createElement('button');
      b.textContent = label;
      if (primary) b.className = 'primary';
      b.addEventListener('click', onClick);
      return b;
    }),
  );
}

const openDashboard = (path = '/') => chrome.tabs.create({ url: BASE + path });

async function run() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url || !isSendable(tab.url)) {
    return show('warn', '!', 'Not a job page', 'Open a job posting in this tab first.');
  }
  const host = new URL(tab.url).host;
  const result = await sendLink(tab.url, tab.title);
  switch (result.kind) {
    case 'added':
      return show('ok', '✓', 'Sent to JobPilot', host, [
        { label: 'Open dashboard', primary: true, onClick: () => openDashboard(`/jobs/${result.id}`) },
      ]);
    case 'duplicate':
      return show('info', '=', 'Already in JobPilot', host, [
        { label: 'Open job', primary: true, onClick: () => openDashboard(`/jobs/${result.id}`) },
      ]);
    case 'unpaired':
      return show('warn', '🔑', 'Pair with your dashboard', 'One time only. It opens a page that connects this extension.', [
        { label: 'Pair now', primary: true, onClick: () => openDashboard('/api/v1/capture/pair') },
      ]);
    case 'offline':
      return show('bad', '×', 'Dashboard is not running', `Nothing is listening at ${BASE}. Start JobPilot, then try again.`, [
        { label: 'Try again', primary: true, onClick: run },
      ]);
    default:
      return show('bad', '×', 'Could not send', result.message ?? 'Unexpected error.', [
        { label: 'Try again', primary: true, onClick: run },
      ]);
  }
}

run();
