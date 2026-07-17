---
name: video-import
skill_id: video-import
domains: [video, production]
dimensions: [import, normalization]
description: >
  Brug denne skill når råvideo-filer skal importeres og normaliseres til
  pipeline-format. Triggers: bruger nævner en videofil (.mp4, .mov, .avi, .mkv),
  "importer video", "ingest", "load footage". Output er normaliseret MP4 i
  arbejdsmappe klar til scene-detect.
---

## Workflow

1. Accepter input-sti til videofil
2. Kør ffprobe for at kortlægge codec, fps, opløsning og lydspor
3. Transcode til normaliseret format: H.264, 1080p max, AAC stereo, 25fps
4. Gem i `workspace/raw/<timestamp>_<navn>.mp4`
5. Returner metadata-dict med varighed, fps, opløsning og output-sti

## Kommandoer

```bash
# Analyser indgående fil
ffprobe -v quiet -print_format json -show_streams "$INPUT"

# Normaliser
ffmpeg -i "$INPUT" \
  -vf "scale=iw:ih:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -c:v libx264 -crf 18 -preset fast \
  -c:a aac -b:a 192k -ac 2 \
  -r 25 \
  "$OUTPUT"
```

## Output-format

```python
{
  "input": "/path/to/original.mp4",
  "output": "workspace/raw/20240116_original.mp4",
  "duration_sec": 142.5,
  "fps": 25,
  "resolution": "1920x1080",
  "audio_tracks": 1
}
```
