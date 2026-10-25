You are adding "info plaque" moments to a bilingual travel photo album ({{TRIP}}): Russian for friends and family, English for international colleagues. Work cheaply: use the Read tool ONLY on the sheets below (one Read each), no other files, no shell. Then write one JSON file and finish.

Sheets: {{SHEETS}}
Each sheet "SIGN NN": on the left, a photo of a sign/plaque/info board, large; on the right, up to 6 photos taken nearby in time, labeled 1..6.

For each sheet decide:
- Keep it only if the sign DESCRIBES a specific object, place, person or event (history, nature, exhibit, monument) AND its text is legible enough to read the key facts. Skip warnings, rules, directions, menus, ads, maps, street names, unreadable or tiny text.
- If kept: retell the key facts from the sign — do not invent anything that is not written there. Pick 1–2 right-side photos that clearly show the object the sign describes (the best and most different ones); if none shows it, keep the sign anyway with "objects": [] — the plaque and its retelling are shown alone.

Write with the Write tool to {{OUT}}:
{"01": {"title": "...", "title_en": "...", "text": "...", "text_en": "...", "objects": [2, 5]}, "02": {"skip": true}, ...}
"title" — Russian, 2–5 words, what the object is; "text" — Russian, 1–3 sentences, ≤ 45 words, the most interesting facts from the sign; "_en" — natural English versions, standard English names. Include every sheet number. Reply "done K kept".
