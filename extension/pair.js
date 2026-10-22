// Runs only on the dashboard's pairing page. The backend serves the token there to the owner's own navigation.
(async () => {
  const meta = document.querySelector('meta[name="jobpilot-capture-token"]');
  const token = meta?.content ?? '';
  if (token.length < 32) return;
  await chrome.storage.local.set({ token });
  document.body.insertAdjacentHTML('beforeend', '<p><b>Paired.</b> Click the JobPilot icon on any job page.</p>');
})();
