# JobPilot browser extension

One click sends the job page you are on to your local JobPilot dashboard, which then processes it.

## Install (once)
1. Make sure JobPilot is running (`pnpm start`) so `http://127.0.0.1:4100` answers.
2. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → choose this `extension/` folder.
3. Click the JobPilot icon on any page → **Pair now**. A tab opens and closes the loop; you only do this once.

## Use
- Open a job posting and click the icon (or press **Alt+Shift+J**). It shows ✓ Sent, or "Already in JobPilot".
- Right-click any link, or the page, → **Send to JobPilot**, to queue links straight from a job-board listing.
  The result appears as a badge on the toolbar icon.

## Security
- It can do exactly one thing: add a link. It uses its own random token (`data/.capture-token`, mode 0600), not
  your dashboard session, so it cannot approve, submit or read anything.
- It only talks to `http://127.0.0.1:4100` and only reads the active tab's address when you click it.
- Pairing hands over the token only to a page you open yourself; another website cannot fetch it.
- If the token is ever refused (for example you deleted `data/.capture-token`), the extension asks you to pair again.
