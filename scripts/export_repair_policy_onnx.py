from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from aesa.experts.ssrn.repair_policy_features import FEATURE_SCHEMA_VERSION


def export_onnx(*, npz_path: str | Path, onnx_path: str | Path) -> None:
    try:
        import onnx
        from onnx import TensorProto, helper, numpy_helper
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("onnx package is required for export") from exc

    source = Path(npz_path)
    if not source.exists():
        raise FileNotFoundError(f"NPZ checkpoint not found: {source}")
    target = Path(onnx_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    data = np.load(source, allow_pickle=True)
    w1 = np.asarray(data["w1"], dtype=np.float32)  # [D,H]
    b1 = np.asarray(data["b1"], dtype=np.float32)  # [H]
    w2 = np.asarray(data["w2"], dtype=np.float32)  # [H,1]
    b2 = np.asarray(data["b2"], dtype=np.float32)  # [1]
    meta_raw = ""
    meta_obj = data.get("meta")
    if meta_obj is not None and len(meta_obj) > 0:
        meta_raw = str(meta_obj[0])
    npz_meta = json.loads(meta_raw) if meta_raw else {}

    input_dim = int(w1.shape[0])

    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, ["N", input_dim])
    y = helper.make_tensor_value_info("scores", TensorProto.FLOAT, ["N"])

    nodes = [
        helper.make_node("MatMul", inputs=["x", "w1"], outputs=["mm1"]),
        helper.make_node("Add", inputs=["mm1", "b1"], outputs=["z1"]),
        helper.make_node("Relu", inputs=["z1"], outputs=["a1"]),
        helper.make_node("MatMul", inputs=["a1", "w2"], outputs=["mm2"]),
        helper.make_node("Add", inputs=["mm2", "b2"], outputs=["scores2d"]),
        helper.make_node("Squeeze", inputs=["scores2d"], outputs=["scores"], axes=[1]),
    ]

    initializers = [
        numpy_helper.from_array(w1, "w1"),
        numpy_helper.from_array(b1, "b1"),
        numpy_helper.from_array(w2, "w2"),
        numpy_helper.from_array(b2, "b2"),
    ]

    graph = helper.make_graph(
        nodes=nodes,
        name="repair_policy_ranker",
        inputs=[x],
        outputs=[y],
        initializer=initializers,
    )
    model = helper.make_model(graph, producer_name="babyai.repair_policy")
    model.opset_import[0].version = 17
    model.metadata_props.extend(
        [
            onnx.StringStringEntryProto(
                key="model_version",
                value=str(npz_meta.get("version", "repair_policy_v1")),
            ),
            onnx.StringStringEntryProto(
                key="feature_dim",
                value=str(input_dim),
            ),
            onnx.StringStringEntryProto(
                key="feature_schema_version",
                value=str(npz_meta.get("feature_schema_version", FEATURE_SCHEMA_VERSION)),
            ),
        ]
    )
    onnx.checker.check_model(model)
    onnx.save_model(model, str(target))


def main() -> int:
    parser = argparse.ArgumentParser(description="Export repair policy checkpoint (.npz) to ONNX.")
    parser.add_argument("--npz", required=True, help="Input checkpoint path.")
    parser.add_argument("--out", required=True, help="Output ONNX path.")
    args = parser.parse_args()

    export_onnx(npz_path=args.npz, onnx_path=args.out)
    print(f"[repair-policy-export] onnx={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
