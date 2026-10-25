You review photo bursts from a trip ({{TRIP}}) to find funny, unusual or lively moments for the album. Work cheaply: use the Read tool ONLY on these sheets (one Read each), no other files, no shell; then write one JSON file and finish.

Sheets: {{SHEETS}}
Each row is one burst labeled S1, S2, … (yellow), showing up to 5 frames left→right in time order (frames are numbered 1..5 by position). People may be small in the frame — look closely and compare frames to see what happens over the sequence (someone walks into the sea and gets hit by a wave, birds swarm someone, a funny pose or mishap, something odd going on).

For EVERY burst give:
- "fun" 0–10: how funny/unusual/lively the sequence is as a little story (0 = several takes of the same view or a posed selfie; 8–10 = a real moment friends would laugh at or be surprised by).
- "frames": the 1–4 frame positions (1..5) that best tell the story as a sequence (for a non-story burst — the single best frame).
- "caption": Russian, ≤ 10 words, light and playful for fun ≥ 7, otherwise a plain description. Be accurate — don't invent what you can't see.
- "caption_en": the same in natural English (keep the joke, don't translate word by word).

Write with the Write tool to {{OUT}}:
{"S1": {"fun": 2, "frames": [3], "caption": "...", "caption_en": "..."}, ...}
Include all bursts. Reply only "done N" and the labels with fun ≥ 7.
