# Workday
- Start at DATA `link` (it ends in `/apply/applyManually`). Never use "Autofill with Resume" or "Use My Last Application".
- Steps: Create Account / Sign In, My Information, My Experience, Application Questions, Voluntary Disclosures, Review. The account step is ACCOUNT WALL.
- Controls carry `data-automation-id`s; segmented dates, comboboxes and repeating sections (work history, education) need a `snapshot` after each edit.
- `Save and Continue` / `Next` only advance a step; they are never the final submit. Voluntary Disclosures and self-identification answers come only from saved answers, otherwise ask.
- On the step named Review the normal submit rule applies.
- Country defaults to the browser's location and is often wrong. Set Country, and the phone Country Phone Code, from DATA `profile.country` (United States = +1) without asking; the saved phone is a US number. Use `profile.address_line1`, `address_line2`, `postal_code`, `city`, `state`.
- For "How Did You Hear About Us?" open the list group and pick a leaf entry, for example the employer's own careers website, or the job board named in DATA; never stop to ask when a close option exists.
