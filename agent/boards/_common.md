# Shared recipe (every board)
- Custom dropdowns, typeaheads and date pickers: `snapshot` after each edit and read the committed value back; if it does not commit after one retry, leave it and ask the owner (`needs_answers` or `blocked`), never guess.
- Trust the page's own validation errors over a visual asterisk.
- CAPTCHA, MFA, passkeys, unclear consent, or a "blocked as spam/abuse" message: stop and return `blocked` with a code-free note. Do not retry from another tab.
- Accounts and passwords: ACCOUNT WALL in NEO.md (the server owns every password).
