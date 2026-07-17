---
name: video-edit
skill_id: video-edit
domains: [video, production]
dimensions: [editing, assembly]
description: >
  Brug denne skill til at klippe og samle video fra scene-liste. Dette er
  stedet filmen klippes sammen. Triggers: "klip video", "saml film", "eksporter",
  "edit", "trim", "concat", "sæt scener sammen". Input er output fra
  video-scene-detect og evt. ComfyUI frames og voice-overlay lyd.
---

## Workflow

1. Modtag scene-liste (fra video-scene-detect)
2. Modtag evt. billeder/frames fra ComfyUI (IMAGE_COMPLETE event)
3. Modtag evt. lydfil fra voice-overlay skill
4. Klip hver scene ud med ffmpeg
5. Concat alle clips i rækkefølge
6. Blend ekstern lyd ind hvis tilgængeligt
7. Eksporter final film til `workspace/output/`

## Kommandoer

```bash
# Klip én scene
ffmpeg -i "$INPUT" -ss "$START" -to "$END" -c copy "workspace/clips/scene_$IDX.mp4"

# Lav concat-fil
for f in workspace/clips/scene_*.mp4; do echo "file '$f'"; done > concat.txt

# Saml film
ffmpeg -f concat -safe 0 -i concat.txt -c copy "workspace/output/film_draft.mp4"

# Blend lyd (voice-overlay eller musik)
ffmpeg -i "workspace/output/film_draft.mp4" \
       -i "workspace/audio/overlay.wav" \
       -filter_complex "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2" \
       -c:v copy \
       "workspace/output/film_final.mp4"
```

## Integration med ComfyUI

Frames fra ComfyUI (IMAGE_COMPLETE events) gemmes i `workspace/frames/`.
Inkluder dem som slideshows med:

```bash
ffmpeg -r 24 -pattern_type glob -i 'workspace/frames/*.png' \
  -c:v libx264 -pix_fmt yuv420p workspace/clips/generated_visuals.mp4
```

## Output

```python
{"output": "workspace/output/film_final.mp4", "duration_sec": 89.3}
```
