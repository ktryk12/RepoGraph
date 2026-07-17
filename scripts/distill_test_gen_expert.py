from __future__ import annotations

from scripts.distill_code_gen_expert import main_with_defaults


def main() -> int:
    return main_with_defaults(
        expert_id="test_gen",
        default_out_dir="models/ssrn/test_gen/distill",
    )


if __name__ == "__main__":
    raise SystemExit(main())
