You are scoring photos{{VIDEOS_WORD}} for a travel album ({{TRIP}}). Work cheaply: use the Read tool ONLY on the contact sheets listed below (one Read each), no other files, no shell. Then write one JSON file and finish.

Photo sheets: {{SHEETS}}
Each is a 5×5 grid in time order; every thumbnail has a yellow label like "5-23". Consecutive labels are often a burst — look at them together to spot a moment unfolding.
{{VIDEO_BLOCK}}
Give EVERY label two independent scores:
1) "score" 1–10 — beauty/clarity: would a friend instantly understand and enjoy it? High: clear recognizable subject, landmarks, strong sense of place, good light and composition, people with a great background. Low: unclear, shot through a car/plane window, signs/maps/screens/tickets, random walls, parking lots, dark, accidental frames.{{VIDEO_SCORE}}
2) "fun" 0–10 — funny, unusual, weird, surprising or lively moment, REGARDLESS of technical quality (blur is fine!). High: someone getting hit/soaked by a wave, birds swarming a person, a funny face or pose, an absurd or strange sight (odd sign, weird sculpture, driverless car, strange animal behavior), a mishap, a spontaneous candid moment. 0 for ordinary views and plain posed selfies.

Write with the Write tool to {{OUT}}:
{"5-23": {"score": 7, "fun": 0, "what": "bridge from cliff viewpoint"}, ...}
"what" = 3–10 English words on the content (for fun moments say what is funny; for videos what happens). Name a landmark only if clearly recognizable. Include every label. Reply just "done N".
