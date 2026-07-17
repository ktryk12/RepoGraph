from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from typing import Any

from babyai.learning.report_generator import ReportGenerator
from babyai.memory.virtual_memory import VirtualMemory
from babyai.memory.visual_memory import VisualMemory
from babyai.memory.voice_memory import VoiceMemory
from babyai.projects.manager import ProjectManager
from babyai.tools.consistency_agent import ConsistencyAgent
from babyai.tools.content_policy import ContentPolicy
from babyai.tools.media_composer import MediaComposer
from babyai.tools.screen_reader import ScreenReader
from babyai.tools.voice_sequence import VoiceSequence
from babyai.tools.voice_tool import VoiceTool
from babyai.tools.visual_tool import VisualTool
from babyai.tools.visual_workflow import VisualWorkflow


_STYLE_CHOICES = ["safe", "photo", "artistic", "nsfw", "video"]
_EXPORT_CHOICES = ["gif", "mp4", "pdf"]
_VOICE_LANG_CHOICES = ["da", "en"]
_VOICE_EXPORT_CHOICES = ["mp3", "wav", "srt"]
_MEDIA_EXPORT_CHOICES = ["pdf", "video"]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    manager = ProjectManager(
        db_path=args.db_path,
        session_path=args.session_file,
    )
    memory = manager.memory

    try:
        payload = _dispatch(args=args, manager=manager, memory=memory)
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True, sort_keys=True))
        return 2

    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babyai")
    parser.add_argument(
        "--db-path",
        default="state/babyai_memory.sqlite",
        help="SQLite path for projects and memory storage.",
    )
    parser.add_argument(
        "--session-file",
        default="~/.babyai_session",
        help="Path for active project session file.",
    )
    parser.add_argument(
        "--image-service-url",
        default="http://localhost:8099",
        help="Base URL for image service.",
    )
    parser.add_argument(
        "--voice-service-url",
        default="http://localhost:8098",
        help="Base URL for voice service proxy.",
    )
    parser.add_argument(
        "--visual-storage-path",
        default="outputs",
        help="Root path for visual storage.",
    )
    parser.add_argument(
        "--voice-storage-path",
        default="outputs",
        help="Root path for voice storage.",
    )

    sub = parser.add_subparsers(dest="entity", required=True)

    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="action", required=True)

    project_create = project_sub.add_parser("create")
    project_create.add_argument("--name", required=True)
    project_create.add_argument("--domains", nargs="+", required=True)

    project_sub.add_parser("list")

    project_switch = project_sub.add_parser("switch")
    project_switch.add_argument("--project-id", required=True)

    project_status = project_sub.add_parser("status")
    project_status.add_argument("--project-id")

    project_fork = project_sub.add_parser("fork")
    project_fork.add_argument("--source-project-id", required=True)
    project_fork.add_argument("--new-name", required=True)

    project_delete = project_sub.add_parser("delete")
    project_delete.add_argument("--project-id", required=True)

    memory = sub.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="action", required=True)

    memory_show = memory_sub.add_parser("show")
    memory_show.add_argument("--project-id", required=True)
    memory_show.add_argument("--domain", required=True)
    memory_show.add_argument("--n", type=int, default=10)

    memory_clear = memory_sub.add_parser("clear")
    memory_clear.add_argument("--project-id", required=True)
    memory_clear.add_argument("--domain", required=True)
    memory_clear.add_argument("--layer", required=True, choices=["working", "knowledge", "all"])

    memory_import = memory_sub.add_parser("import")
    memory_import.add_argument("--source-project-id", required=True)
    memory_import.add_argument("--domain", required=True)
    memory_import.add_argument("--target-project-id", required=True)

    memory_snapshot = memory_sub.add_parser("snapshot")
    memory_snapshot.add_argument("--project-id", required=True)

    learning = sub.add_parser("learning")
    learning_sub = learning.add_subparsers(dest="action", required=True)
    learning_report = learning_sub.add_parser("report")
    learning_report.add_argument("--project")

    visual = sub.add_parser("visual")
    visual.add_argument("--project-id")
    visual_sub = visual.add_subparsers(dest="action", required=True)

    visual_generate = visual_sub.add_parser("generate")
    visual_generate.add_argument("prompt")
    visual_generate.add_argument("--style", choices=_STYLE_CHOICES, default="safe")
    visual_generate.add_argument("--sequence")

    visual_analyze = visual_sub.add_parser("analyze")
    visual_analyze.add_argument("image_path")
    visual_analyze.add_argument("question")

    visual_edit = visual_sub.add_parser("edit")
    visual_edit.add_argument("image_path")
    visual_edit.add_argument("instruction")
    visual_edit.add_argument("--style", choices=_STYLE_CHOICES, default="safe")

    visual_sequence = visual_sub.add_parser("sequence")
    visual_sequence_sub = visual_sequence.add_subparsers(dest="sequence_action", required=True)

    visual_sequence_create = visual_sequence_sub.add_parser("create")
    visual_sequence_create.add_argument("name")
    visual_sequence_create.add_argument("--style", choices=_STYLE_CHOICES, required=True)

    visual_sequence_add = visual_sequence_sub.add_parser("add")
    visual_sequence_add.add_argument("sequence_id")
    visual_sequence_add.add_argument("prompt")

    visual_sequence_show = visual_sequence_sub.add_parser("show")
    visual_sequence_show.add_argument("sequence_id")

    visual_sequence_export = visual_sequence_sub.add_parser("export")
    visual_sequence_export.add_argument("sequence_id")
    visual_sequence_export.add_argument("--format", choices=_EXPORT_CHOICES, required=True)

    voice = sub.add_parser("voice")
    voice.add_argument("--project-id")
    voice_sub = voice.add_subparsers(dest="action", required=True)

    voice_speak = voice_sub.add_parser("speak")
    voice_speak.add_argument("text")
    voice_speak.add_argument("--voice", dest="voice_id")
    voice_speak.add_argument("--lang", choices=_VOICE_LANG_CHOICES, default="da")
    voice_speak.add_argument("--sequence")

    voice_clone = voice_sub.add_parser("clone")
    voice_clone.add_argument("text")
    voice_clone.add_argument("--voice", dest="voice_id", required=True)
    voice_clone.add_argument("--lang", choices=_VOICE_LANG_CHOICES, default="da")

    voice_register = voice_sub.add_parser("register")
    voice_register.add_argument("name")
    voice_register.add_argument("--sample", required=True)
    voice_register.add_argument("--lang", choices=_VOICE_LANG_CHOICES, default="da")

    voice_sub.add_parser("voices")

    voice_transcribe = voice_sub.add_parser("transcribe")
    voice_transcribe.add_argument("audio_path")
    voice_transcribe.add_argument("--lang", choices=_VOICE_LANG_CHOICES)

    voice_screen = voice_sub.add_parser("screen")
    voice_screen.add_argument("--duration", type=float, default=30.0)

    voice_sequence = voice_sub.add_parser("sequence")
    voice_sequence_sub = voice_sequence.add_subparsers(dest="sequence_action", required=True)

    voice_sequence_create = voice_sequence_sub.add_parser("create")
    voice_sequence_create.add_argument("name")
    voice_sequence_create.add_argument("--voice", dest="voice_id", required=True)
    voice_sequence_create.add_argument("--lang", choices=_VOICE_LANG_CHOICES, default="da")

    voice_sequence_add = voice_sequence_sub.add_parser("add")
    voice_sequence_add.add_argument("seq_id")
    voice_sequence_add.add_argument("text")

    voice_sequence_export = voice_sequence_sub.add_parser("export")
    voice_sequence_export.add_argument("seq_id")
    voice_sequence_export.add_argument("--format", choices=_VOICE_EXPORT_CHOICES, required=True)

    media = sub.add_parser("media")
    media.add_argument("--project-id")
    media_sub = media.add_subparsers(dest="action", required=True)

    media_scene = media_sub.add_parser("scene")
    media_scene_sub = media_scene.add_subparsers(dest="scene_action", required=True)

    media_scene_create = media_scene_sub.add_parser("create")
    media_scene_create.add_argument("name")
    media_scene_create.add_argument("--style", choices=_STYLE_CHOICES, required=True)
    media_scene_create.add_argument("--voice", dest="voice_id", required=True)

    media_scene_add = media_scene_sub.add_parser("add")
    media_scene_add.add_argument("seq_id")
    media_scene_add.add_argument("visual_prompt")
    media_scene_add.add_argument("narration_text")

    media_scene_export = media_scene_sub.add_parser("export")
    media_scene_export.add_argument("seq_id")
    media_scene_export.add_argument("--format", choices=_MEDIA_EXPORT_CHOICES, required=True)

    return parser


def _dispatch(args: argparse.Namespace, *, manager: ProjectManager, memory: VirtualMemory) -> dict[str, Any]:
    entity = str(args.entity)
    action = str(args.action)

    if entity == "project":
        if action == "create":
            project_id = manager.create(name=args.name, domains=list(args.domains))
            return {"project_id": project_id}
        if action == "list":
            return {"projects": manager.list()}
        if action == "switch":
            manager.switch(args.project_id)
            return {"project_id": str(args.project_id), "active": True}
        if action == "status":
            project_id = args.project_id
            if project_id is None:
                project_id = manager.active_project_id()
                if not project_id:
                    raise FileNotFoundError("no active project in session file")
            project = manager.get(str(project_id))
            if project is None:
                raise ValueError(f"project not found: {project_id}")
            return {
                "active_project_id": manager.active_project_id(),
                "project": project,
                "snapshot": memory.snapshot(str(project_id)),
                "active_episodes": manager.get_active_episodes(str(project_id)),
            }
        if action == "fork":
            project_id = manager.fork(
                source_project_id=args.source_project_id,
                new_name=args.new_name,
            )
            return {"project_id": project_id}
        if action == "delete":
            manager.delete(args.project_id)
            return {"deleted": str(args.project_id)}

    if entity == "memory":
        if action == "show":
            context = memory.get_context(args.project_id, args.domain, n=args.n)
            return {"project_id": str(args.project_id), "domain": str(args.domain), "context": context}
        if action == "clear":
            memory.clear(args.project_id, args.domain, args.layer)
            return {
                "project_id": str(args.project_id),
                "domain": str(args.domain),
                "layer": str(args.layer),
                "cleared": True,
            }
        if action == "import":
            imported = memory.import_from(
                source_project_id=args.source_project_id,
                domain=args.domain,
                target_project_id=args.target_project_id,
            )
            return {"imported": int(imported)}
        if action == "snapshot":
            return memory.snapshot(args.project_id)

    if entity == "learning":
        if action == "report":
            project_id = str(args.project or "").strip()
            if not project_id:
                active = manager.active_project_id()
                if not active:
                    raise FileNotFoundError("no active project in session file")
                project_id = str(active)
            project = manager.get(project_id)
            if project is None:
                raise ValueError(f"project not found: {project_id}")
            report = ReportGenerator(project_id=project_id, memory_ref=memory).weekly_report()
            if is_dataclass(report):
                return asdict(report)
            return dict(report)

    if entity == "visual":
        project_id = _resolve_project_id(manager=manager, explicit_project_id=args.project_id)
        stack = _build_media_stack(args=args, project_id=project_id)

        if action == "generate":
            result = stack["visual_tool"].generate_image(
                prompt=args.prompt,
                style_profile=args.style,
                sequence_id=args.sequence,
            )
            return asdict(result)

        if action == "analyze":
            result = stack["visual_tool"].analyze_image(
                image_ref=args.image_path,
                question=args.question,
            )
            return asdict(result)

        if action == "edit":
            result = stack["visual_tool"].edit_image(
                image_ref=args.image_path,
                instruction=args.instruction,
                style_profile=args.style,
            )
            return asdict(result)

        if action == "sequence":
            sequence_action = str(args.sequence_action)
            if sequence_action == "create":
                sequence_id = stack["visual_workflow"].create_sequence(
                    name=args.name,
                    style_profile=args.style,
                    project_id=project_id,
                )
                return {"sequence_id": sequence_id}
            if sequence_action == "add":
                result = stack["visual_workflow"].add_frame(args.sequence_id, args.prompt)
                return asdict(result)
            if sequence_action == "show":
                summary = stack["visual_workflow"].get_sequence_summary(args.sequence_id)
                return asdict(summary)
            if sequence_action == "export":
                file_path = stack["visual_workflow"].export_sequence(args.sequence_id, args.format)
                return {"sequence_id": args.sequence_id, "file_path": file_path, "format": args.format}

    if entity == "voice":
        project_id = _resolve_project_id(manager=manager, explicit_project_id=args.project_id)
        stack = _build_media_stack(args=args, project_id=project_id)

        if action == "speak":
            if args.voice_id:
                result = stack["voice_tool"].clone_voice(
                    text=args.text,
                    voice_id=args.voice_id,
                    language=args.lang,
                )
            else:
                result = stack["voice_tool"].speak(
                    text=args.text,
                    voice_profile="default",
                    language=args.lang,
                    sequence_id=args.sequence,
                )
            return asdict(result)

        if action == "clone":
            result = stack["voice_tool"].clone_voice(
                text=args.text,
                voice_id=args.voice_id,
                language=args.lang,
            )
            return asdict(result)

        if action == "register":
            voice_id = stack["voice_tool"].register_voice(
                name=args.name,
                sample_path=args.sample,
                language=args.lang,
            )
            return {"voice_id": voice_id}

        if action == "voices":
            voices = stack["voice_tool"].list_voices()
            return {"voices": [asdict(item) for item in voices]}

        if action == "transcribe":
            result = stack["voice_tool"].transcribe(
                audio_path=args.audio_path,
                language=args.lang,
            )
            return asdict(result)

        if action == "screen":
            screen_reader = ScreenReader(
                voice_service_url=args.voice_service_url,
                voice_memory=stack["voice_memory"],
                project_id=project_id,
                project_policy={"allow_screen_capture": True},
                request_fn=lambda method, path, payload, timeout: stack["voice_tool"]._request_json(method, path, payload),
            )
            result = screen_reader.capture_and_transcribe(duration_seconds=args.duration)
            return asdict(result)

        if action == "sequence":
            sequence_action = str(args.sequence_action)
            if sequence_action == "create":
                seq_id = stack["voice_sequence"].create_sequence(
                    name=args.name,
                    voice_id=args.voice_id,
                    language=args.lang,
                    project_id=project_id,
                )
                return {"seq_id": seq_id}
            if sequence_action == "add":
                result = stack["voice_sequence"].add_segment(args.seq_id, args.text)
                return asdict(result)
            if sequence_action == "export":
                file_path = stack["voice_sequence"].export_sequence(args.seq_id, args.format)
                return {"seq_id": args.seq_id, "file_path": file_path, "format": args.format}

    if entity == "media":
        project_id = _resolve_project_id(manager=manager, explicit_project_id=args.project_id)
        stack = _build_media_stack(args=args, project_id=project_id)

        if action == "scene":
            scene_action = str(args.scene_action)
            if scene_action == "create":
                seq_id = stack["media_composer"].create_narrated_sequence(
                    name=args.name,
                    style_profile=args.style,
                    voice_id=args.voice_id,
                    project_id=project_id,
                )
                return {"seq_id": seq_id}
            if scene_action == "add":
                result = stack["media_composer"].add_scene(
                    seq_id=args.seq_id,
                    visual_prompt=args.visual_prompt,
                    narration_text=args.narration_text,
                )
                return asdict(result)
            if scene_action == "export":
                file_path = stack["media_composer"].export(args.seq_id, args.format)
                return {"seq_id": args.seq_id, "file_path": file_path, "format": args.format}

    raise ValueError(f"unknown command: {entity} {action}")


def _build_media_stack(*, args: argparse.Namespace, project_id: str) -> dict[str, Any]:
    visual_memory = VisualMemory(
        project_id=project_id,
        storage_path=args.visual_storage_path,
        db_ref=args.db_path,
    )
    voice_memory = VoiceMemory(
        project_id=project_id,
        storage_path=args.voice_storage_path,
        db_ref=args.db_path,
    )

    content_policy = ContentPolicy(
        project_id=project_id,
        policy_config={"entrypoint": "image_service"},
        config_guard_ref=lambda **_: None,
    )

    consistency_agent = ConsistencyAgent(
        visual_memory=visual_memory,
        image_service_url=args.image_service_url,
        project_id=project_id,
    )
    visual_tool = VisualTool(
        image_service_url=args.image_service_url,
        content_policy=content_policy,
        visual_memory=visual_memory,
        project_id=project_id,
        consistency_agent=consistency_agent,
    )
    visual_workflow = VisualWorkflow(
        visual_tool=visual_tool,
        visual_memory=visual_memory,
        consistency_agent=consistency_agent,
    )

    voice_tool = VoiceTool(
        voice_service_url=args.voice_service_url,
        content_policy=content_policy,
        voice_memory=voice_memory,
        project_id=project_id,
    )
    voice_sequence = VoiceSequence(
        voice_tool=voice_tool,
        voice_memory=voice_memory,
    )

    media_composer = MediaComposer(
        visual_workflow=visual_workflow,
        voice_sequence=voice_sequence,
        voice_memory=voice_memory,
        visual_memory=visual_memory,
    )

    return {
        "visual_memory": visual_memory,
        "voice_memory": voice_memory,
        "visual_tool": visual_tool,
        "visual_workflow": visual_workflow,
        "voice_tool": voice_tool,
        "voice_sequence": voice_sequence,
        "media_composer": media_composer,
    }


def _resolve_project_id(*, manager: ProjectManager, explicit_project_id: str | None) -> str:
    project_id = str(explicit_project_id or "").strip()
    if not project_id:
        active = manager.active_project_id()
        if not active:
            raise FileNotFoundError("no active project in session file")
        project_id = str(active)
    if manager.get(project_id) is None:
        raise ValueError(f"project not found: {project_id}")
    return project_id


if __name__ == "__main__":
    raise SystemExit(main())
