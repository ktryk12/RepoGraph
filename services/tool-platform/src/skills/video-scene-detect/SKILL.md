---
name: video-scene-detect
skill_id: video-scene-detect
domains: [video, production]
dimensions: [detection, analysis]
description: >
  Brug denne skill til at detektere scene-skift og generere kapitler i en
  video. Triggers: "find klip", "scene detection", "lav kapitler", "split video",
  "find cuts". Input er normaliseret MP4 fra video-import skill.
---

## Workflow

1. Modtag video-sti fra video-import output
2. Kør PySceneDetect eller ffmpeg scene-filter
3. Generer liste over scene-timestamps
4. Kald LLM (via CLAUDE_MODEL) for at foreslå kapitel-navne baseret på indhold
5. Returner struktureret scene-liste klar til video-edit skill

## Kommandoer

```bash
# ffmpeg scene-detektion (threshold 0.3 = moderat sensitivitet)
ffmpeg -i "$INPUT" \
  -filter:v "select='gt(scene,0.3)',showinfo" \
  -f null - 2>&1 | grep "pts_time"

# Alternativt: PySceneDetect
scenedetect -i "$INPUT" detect-content list-scenes
```

## Scene-liste format

```python
[
  {"index": 0, "start_sec": 0.0,   "end_sec": 12.4,  "title": "Intro"},
  {"index": 1, "start_sec": 12.4,  "end_sec": 34.8,  "title": "Produkt demo"},
  {"index": 2, "start_sec": 34.8,  "end_sec": 67.2,  "title": "Konklusion"},
]
```

## LLM-kald til kapitel-navne

Send scene-liste + evt. transskription til `generate_script()` med prompt:
> "Giv hvert kapitel et kort beskrivende navn på maks 4 ord baseret på tidsstempler og kontekst."
