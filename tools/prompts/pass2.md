You are the final editor of a travel photo album ({{TRIP}}), bilingual: Russian for friends and family, English for international colleagues. Work cheaply: Read ONLY the contact sheets below (one Read each), no other files, no shell. Then write one JSON file and finish.

Sheets: {{SHEETS}}
On each sheet: photos labeled 1..N (yellow); extra "moment" candidates labeled F1..; each video is a row of 6 frames (start→end) labeled V1, V2…

Context per section (time, place from GPS, first-pass descriptions with score/fun — may be imperfect, trust your eyes). "Already included funny series" = a burst shown automatically in that section on its own row; don't pick photos duplicating it, you may mention it in the description.
{{CONTEXT}}

Rules:
- Up to {{MAX_SECTIONS}} sections; each 3–5 main photos and, where available, 1–2 videos. If there are more sections than that, drop the weakest ("drop": true). Never drop a section that has a funny series unless it is still clearly the weakest.
- Main photos: clear subject, sense of place, moments, people with great backgrounds; never near-identical shots; at most one selfie per section; variety (wide + detail + people). Avoid repeating the same landmark view across sections.
- Videos: keep the ones where something distinctive happens; skip static/shaky/repetitive ones; never two near-identical clips. If the same event appears in two sections, keep it once. Mark a funny/unusual clip "fun": true.
- "fun": 0–2 extra photos per section from F-labels (or a main label via "id_main") that are genuinely funny/unusual/surprising — they go BEYOND the 3–5 limit with a «момент» badge.
- "hero": the strongest landscape photo of the section (a main label), shown full-width.
- "cover": best photo of the whole album, horizontal and iconic: {"s": N, "n": label}; not a section's hero.
- "album_title": short Russian album title (1–3 words, usually the place, e.g. «Сан-Франциско»); "album_title_en": the same in English.
- Russian text: "title" 2–4 words; "desc" one sentence ≤ 20 words; captions ≤ 8 words, accurate (name landmarks only when sure). Fun captions may be playful.
- Every text field also gets a natural English version (not a word-by-word translation): "title_en", "desc_en", "caption_en"; use standard English names of places/landmarks.

Write with the Write tool to {{OUT}} exactly in this shape:
{"album_title": "...", "album_title_en": "...", "cover": {"s": 7, "n": 5}, "s05": {"drop": false, "title": "...", "title_en": "...", "desc": "...", "desc_en": "...", "hero": 1, "photos": [{"n": 1, "caption": "...", "caption_en": "..."}], "fun": [{"n": 1, "caption": "...", "caption_en": "..."}], "videos": [{"v": 1, "caption": "...", "caption_en": "...", "fun": false}]}, ...}
In "fun", "n" is an F-label number (F1 → 1); to mark a main photo use {"id_main": 3, "caption": "..."}. Include all section keys listed above. Reply only "done" plus dropped sections.
