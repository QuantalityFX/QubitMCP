"""Repeatable native GPU runs and a local image-quality review report.

Run with the application's Python, after native Setup. Outputs default to logs/.
--plan-only prepares cases without loading a model or accepting a license.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import html
import json
from pathlib import Path
import threading

from .config import storage_root
from .native_backend import NativeBackend, NativeService
from .native_config import MODEL_ID, MODEL_REVISION, saved_native_config
from .schema import ImageRequest


def quality_cases(smoke=False, edit_image=None, reference_image=None):
    cases = [
        ("photograph", "A studio product photograph of a red ceramic teapot on a pale oak table, "
         "soft window light from the left, detailed ceramic glaze and realistic reflections.",
         ["Red ceramic teapot and pale oak table match the prompt", "Fine glaze detail, coherent lighting and reflections"]),
        ("typography", 'A clear storefront sign reading exactly "QUBIT FIELD", dark blue lettering on a cream sign, '
         "daylight, straight-on view, sharp readable text.", ["Exact readable text QUBIT FIELD", "No extra or broken letters"]),
        ("composition", "Three distinct objects in a row on a plain white table: a red cube on the left, "
         "a green glass sphere in the middle, and a blue ceramic cone on the right. Soft shadows.",
         ["Exactly three requested objects", "Correct left-to-right order, colors and shapes"]),
        ("portrait", "A natural editorial portrait of an adult gardener holding a small potted fern with both hands, "
         "soft overcast light, detailed skin and fabric, a garden softly blurred behind them.",
         ["Plausible face, hands and plant", "Natural skin texture and consistent depth of field"]),
        ("rgba", "A full-body cute cartoon dragon sticker with clean edges, "
         "isolated and centered, no cast shadow, no floor, no text.",
         ["Actual non-opaque alpha, visible subject", "Clean edges without a baked-in checkerboard or opaque background"]),
    ]
    if smoke:
        cases = cases[:1]
    result = []
    for index, (name, prompt, checks) in enumerate(cases):
        dimensions = (1792, 2400) if name == "portrait" else (2048, 2048)
        if smoke:
            dimensions = (1024, 1024)
        request = ImageRequest(prompt=prompt, backend="native_diffusers", model_variant="bf16", seed=42 + index,
                               width=dimensions[0], height=dimensions[1], steps=40, cfg=1,
                               transparent=name == "rgba")
        result.append({"name": name, "request": asdict(request), "review_checks": checks})
    if edit_image:
        edits = [("edit", "Change the background to a sunset beach. Preserve the foreground subject's identity, shape and colors.", None)]
        if reference_image:
            edits.append(("two_references", "Place the subject from the first image into the scene from the second image. "
                          "Preserve the subject's identity and the second image's scene layout.", str(Path(reference_image).resolve())))
        for name, prompt, reference in edits:
            request = ImageRequest(prompt=prompt, backend="native_diffusers", model_variant="bf16", mode="image_edit",
                image=str(Path(edit_image).resolve()), reference_image=reference, seed=42, edit_resolution=2048)
            result.append({"name": name, "request": asdict(request),
                           "review_checks": ["Requested change is visible", "Subject identity and unedited details are preserved"]})
    return result


def write_report(directory, report):
    (directory / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    cards = []
    for case in report["cases"]:
        request = case["request"]
        result = case.get("result", {})
        path = result.get("image_path")
        picture = f'<a href="{html.escape(Path(path).name)}"><img src="{html.escape(Path(path).name)}"></a>' if path else ""
        checks = "".join(f"<li>{html.escape(check)}</li>" for check in case["review_checks"])
        details = f"Seed {request['seed']}; {request['steps']} requested steps; BF16; CFG {request['cfg']}"
        if result:
            metadata = result["metadata"]
            details += f"; {metadata['width']} × {metadata['height']}; {metadata['elapsed_seconds']} seconds"
        cards.append(f"<article><h2>{html.escape(case['name'])}</h2><p>{html.escape(case['status'])}</p>"
                     f"{picture}<p>{html.escape(request['prompt'])}</p><p>{html.escape(details)}</p>"
                     f"<ul>{checks}</ul><p>{html.escape(case.get('error', ''))}</p></article>")
    document = ("<!doctype html><meta charset='utf-8'><title>Qwen native quality review</title>"
                "<style>body{font:16px system-ui;background:#17202f;color:#eee;max-width:1200px;margin:32px auto;padding:20px}"
                "article{padding:20px;margin:20px 0;background:#253246;border-radius:8px}img{max-width:100%;max-height:640px}"
                "a{color:#82bbff}p{line-height:1.6}</style><h1>Qwen native quality review</h1>"
                "<p>Manual review pending. Successful inference does not establish visual quality. "
                "Inspect full-size PNGs, alpha edges, prompt accuracy, and edit preservation. "
                "Compare with your saved ComfyUI baseline; matching seeds across runtimes do not imply matching pixels.</p>"
                + "".join(cards))
    (directory / "review.html").write_text(document, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="One 1024-square image at 40 steps before the full quality run")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--low-memory", action="store_true", help="Use sequential offload without changing size or sampling")
    parser.add_argument("--edit-image", type=Path)
    parser.add_argument("--reference-image", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--accept-model-license", action="store_true")
    parser.add_argument("--license-use", choices=("research", "separately_licensed"))
    args = parser.parse_args()
    if not args.plan_only and (not args.accept_model_license or not args.license_use):
        parser.error("A permitted --license-use and --accept-model-license are required to generate.")
    directory = args.output or storage_root() / "logs/qwen_image" / ("native-quality-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True, exist_ok=True)
    cases = quality_cases(args.smoke, args.edit_image, args.reference_image)
    for case in cases:
        case["request"]["low_memory"] = args.low_memory
        case["status"] = "planned; not generated"
    report = {"model_id": MODEL_ID, "model_revision": MODEL_REVISION, "visual_review": "pending",
              "quality_approved": False, "cases": cases}
    write_report(directory, report)
    if args.plan_only:
        print(f"Quality plan written to {directory / 'review.html'}. No model was run.")
        return
    service = NativeService(log=lambda text: print(text, end="", flush=True))
    cancel = threading.Event()
    try:
        # Honor the same inter-window runtime lock as the app; two model workers cannot run by accident.
        from PySide6 import QtCore
        config = saved_native_config()
        import hashlib
        key = hashlib.sha256(str(config.runtime.resolve()).lower().encode()).hexdigest()[:16]
        locks = storage_root() / "logs/qwen_image"
        locks.mkdir(parents=True, exist_ok=True)
        lock = QtCore.QLockFile(str(locks / f"{key}-runtime.lock"))
        if not lock.tryLock(0):
            raise RuntimeError("Native runtime is active in another app window. Stop that backend before the quality run.")
        service.start(config)
        backend = NativeBackend(service)
        report["inventory"] = backend.inventory()
        for case in cases:
            case["status"] = "running"
            write_report(directory, report)
            try:
                result = backend.generate(ImageRequest(**case["request"]), directory, cancel,
                    lambda data: print(data["message"], flush=True))
                case["result"] = {"image_path": str(result.image_path), "metadata": result.metadata}
                case["status"] = "generated; visual review required"
            except Exception as exc:
                case["error"] = str(exc)
                case["status"] = "failed"
                write_report(directory, report)
                raise
            write_report(directory, report)
    except KeyboardInterrupt:
        cancel.set()
        report["interrupted"] = True
        for case in cases:
            if case["status"] == "running":
                case["status"] = "interrupted; not approved"
    finally:
        service.stop()
        if "lock" in locals():
            lock.unlock()
        write_report(directory, report)
    print(f"Review full-size images and settings at {directory / 'review.html'}")


if __name__ == "__main__":
    main()
