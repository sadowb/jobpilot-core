// Right-click menu: send a link (or the current page) without opening the popup. Result shows as a badge.
import { isSendable, sendLink } from './shared.js';

const MENU_ID = 'jobpilot-send';

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: 'Send to JobPilot',
    contexts: ['link', 'page'],
  });
});

async function badge(tabId, text, color) {
  await chrome.action.setBadgeBackgroundColor({ color, tabId });
  await chrome.action.setBadgeText({ text, tabId });
  setTimeout(() => chrome.action.setBadgeText({ text: '', tabId }), 4000);
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU_ID || !tab?.id) return;
  const url = info.linkUrl || info.pageUrl || tab.url;
  if (!isSendable(url)) return badge(tab.id, '!', '#b84337');
  const result = await sendLink(url, tab.title);
  if (result.kind === 'added') return badge(tab.id, '✓', '#3f6236');
  if (result.kind === 'duplicate') return badge(tab.id, '=', '#3b6b78');
  if (result.kind === 'unpaired') return badge(tab.id, 'pair', '#9b5a1a');
  return badge(tab.id, '!', '#b84337');
});
