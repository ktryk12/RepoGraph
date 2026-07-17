---
name: voice-overlay
skill_id: voice-overlay
domains: [video, audio, production]
dimensions: [voice, tts, overlay]
description: >
  Brug denne skill til at generere og blende stemme eller lyde på filmen via
  VoiceIOAgent. Triggers: "tilføj stemme", "voice over", "narrator", "lyd på
  filmen", "generer lyd", "TTS til video". Kræver at VoiceIOAgent er aktiv.
---

## Workflow

1. Modtag tekst-script (fra generate_script() eller manuelt input)
2. Publiser VOICE_OVERLAY_REQUEST event på message bus med teksten
3. VoiceOverlayAgent videresender til VoiceIOAgent via VOICE_OUTPUT
4. VoiceIOAgent taler teksten og gemmer WAV-fil
5. Returner sti til WAV-fil klar til video-edit skill

## Message Bus integration

```python
from babyai_shared.bus.protocol import MessageType

# VideoEditAgent publisher VOICE_OVERLAY_REQUEST
payload = {
    "text": script,
    "output_path": "workspace/audio/overlay.wav",
    "format": "wav",
}
# VoiceOverlayAgent modtager og videresender til VoiceIOAgent (VOICE_OUTPUT)
```

## Brug i video-edit

Video-edit skill modtager output-stien og bruger ffmpeg amix til at blende
voice-over ind over eksisterende lyd (se video-edit SKILL.md).

## Graceful degradation

Hvis VoiceIOAgent ikke er tilgængeligt (service nede), log en advarsel og
returner None — video-edit skill eksporterer film uden voice-over.
