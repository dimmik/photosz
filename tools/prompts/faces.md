You are checking photos of a travel album for people looking at the camera. Work cheaply: use the Read tool ONLY on the sheets below (one Read each), no other files, no shell. Then write one JSON file and finish.

Sheets: {{SHEETS}}
Each is a grid of photos with yellow labels like "P12".

For every photo where at least one person's face is clearly visible, reasonably large and turned toward the camera (selfies, posed photos, someone glancing into the lens), give the number of such people. Ignore tiny distant figures, backs of heads, faces in profile, statues, posters and screens.

Write with the Write tool to {{OUT}}:
{"P3": 2, "P17": 1}
Only labels with at least one such face. Reply "done N".
