"""Coordinate isolated body/hand workers and collect one animation result."""
from __future__ import annotations

from dataclasses import asdict, replace
import json

from .backends.handmdm import HandMDMBackend, HandMotionRequest
from .director import HandIntent, direct_hands
from .hands import add_preview_hands, export_preview_bvh, load_hand_archive, merge_hand_layer
from .storage import load_animation
from .types import GeneratedMotion, GenerationPlan


class BodyHandBackend:
    def __init__(self, body_backend, hand_config, *, side="auto", description="", start=0.0, end=None, blend=0.15):
        self.body = body_backend
        self.config = body_backend.config  # Existing UI path/setup ownership stays body-owned.
        self.hand_config = hand_config
        self.intent_options = dict(side=side, description=description, start=start, end=end, blend=blend)

    def check(self):
        return self.body.check() + HandMDMBackend(self.hand_config).check()

    def prepare(self, request):
        request.validate()
        # PriorMDM uses N samples, whose final sample time is (N-1)/fps.
        clip_end = (round(request.duration * 20) - 1) / 20
        intent = direct_hands(request.prompt, clip_end, **self.intent_options)
        errors = self.check()
        if errors:
            raise ValueError("\n".join(errors))
        body = self.body.prepare(request)
        hand = HandMDMBackend(replace(self.hand_config, output_root=body.run_dir / "hands")).prepare(
            HandMotionRequest(intent.prompt, seed=request.seed))
        manifest = {"schema_version": 1, "intent": asdict(intent),
                    "hand_archive": str((hand.run_dir / "hands.npz").relative_to(body.run_dir))}
        (body.run_dir / "body_hands.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return GenerationPlan(body.command, body.cwd, body.run_dir, body.environment, stages=(body, hand))

    def collect(self, plan):
        manifest = json.loads((plan.run_dir / "body_hands.json").read_text(encoding="utf-8"))
        # Decode/validate hands before writing any derived body files.
        hands = load_hand_archive(plan.run_dir / manifest["hand_archive"])
        body_result = self.body.collect(plan)
        skeleton, body_clip = load_animation(body_result.animation_path)
        skeleton = add_preview_hands(skeleton)
        clip = merge_hand_layer(skeleton, body_clip, hands, HandIntent(**manifest["intent"]))
        clip.metadata.update(hand_geometry=skeleton.metadata["hand_geometry"],
                             body_archive=str(body_result.archive_path), hand_archive=manifest["hand_archive"])
        animation = plan.run_dir / "animation_hands.json"
        bvh = plan.run_dir / "preview_hands.bvh"
        if animation.exists() or bvh.exists():
            raise FileExistsError("Merged output already exists.")
        export_preview_bvh(bvh, skeleton, clip)
        with animation.open("x", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "skeleton": skeleton.to_dict(), "clip": clip.to_dict()}, stream)
        return GeneratedMotion(plan.run_dir, body_result.archive_path, animation, bvh, clip.metadata)
