# loader.py
"""
Central plugin bootstrapper for EchoGraph.
Call bootstrap_plugins() after nodes/ is on sys.path and nodes.core is imported.
"""

from __future__ import annotations
import importlib

# Expect nodes.core to be importable by the time this module is used.
from nodes import core


def _safe_probe(kind: str):
    """Small helper to print spec details safely."""
    try:
        spec = core.get_spec(kind)
        stripe = getattr(spec, "stripe_color", None)
        has_hook = bool(getattr(spec, "augment_infocard_footer", None))
        print(f"[EchoGraph] {kind} spec: {type(spec).__name__} stripe: {stripe} has_hook: {has_hook}")
    except Exception as e:
        print(f"[EchoGraph] core.get_spec('{kind}') failed:", e)


def bootstrap_plugins():
    """Register core defaults and optional node plugins (e.g., Librarian)."""

    # 1) Core defaults (single source of truth)
    try:
        core.register_defaults()
    except Exception as e:
        print("[EchoGraph] register_defaults failed:", e)

    # 2) Librarian
    try:
        from nodes import librarian
        if hasattr(librarian, "register"):
            librarian.register()
            _safe_probe("librarian")
        else:
            print("[EchoGraph] Librarian module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Librarian plugin import failed:", e)

    # 3) Note
    try:
        from nodes import note
        if hasattr(note, "register"):
            note.register()
            _safe_probe("note")
        else:
            print("[EchoGraph] Note module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Note plugin import failed:", e)

    # 4) Python
    try:
        from nodes import python as python_node
        if hasattr(python_node, "register"):
            python_node.register()
            _safe_probe("python")
        else:
            print("[EchoGraph] Python module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Python plugin import failed:", e)

    # 4.5) Gantt Chart
    try:
        from nodes import gantt_chart
        if hasattr(gantt_chart, "register"):
            gantt_chart.register()
            _safe_probe("gantt_chart")
        else:
            print("[EchoGraph] Gantt Chart module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Gantt Chart plugin import failed:", e)

    # 5) Output
    try:
        from nodes import output
        if hasattr(output, "register"):
            output.register()
            _safe_probe("output")
        else:
            print("[EchoGraph] Output module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Output plugin import failed:", e)

    # 6) Append (use importlib to guarantee the submodule gets loaded)
    try:
        append_node = importlib.import_module("nodes.append")
        print("[EchoGraph] append module:", getattr(append_node, "__file__", "<no __file__>"))
        if hasattr(append_node, "register"):
            append_node.register()
            _safe_probe("append")
        else:
            print("[EchoGraph] Append module missing 'register' (got:", dir(append_node), ")")
    except Exception as e:
        print("[EchoGraph] Append plugin import failed:", e)

    # 7) Switch
    try:
        from nodes import switch as switch_node
        if hasattr(switch_node, "register"):
            switch_node.register()
            _safe_probe("switch")
        else:
            print("[EchoGraph] Switch module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Switch plugin import failed:", e)

    # 8) HTML Preview
    try:
        from nodes import html_preview
        if hasattr(html_preview, "register"):
            html_preview.register()
            _safe_probe("html_preview")
        else:
            print("[EchoGraph] HTML Preview module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] HTML Preview plugin import failed:", e)

    # 8.5) Primitive
    try:
        from nodes import primitive
        if hasattr(primitive, "register"):
            primitive.register()
            _safe_probe("primitive")
        else:
            print("[EchoGraph] Primitive module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Primitive plugin import failed:", e)

    # 8.6) UV Unwrap
    try:
        from nodes import uv_unwrap
        if hasattr(uv_unwrap, "register"):
            uv_unwrap.register()
            _safe_probe("uv_unwrap")
        else:
            print("[EchoGraph] UV Unwrap module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] UV Unwrap plugin import failed:", e)

    # 8.7) Texture
    try:
        from nodes import texture
        if hasattr(texture, "register"):
            texture.register()
            _safe_probe("texture")
        else:
            print("[EchoGraph] Texture module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Texture plugin import failed:", e)

    # 8.8) Texture Pro
    try:
        from nodes import texture_pro
        if hasattr(texture_pro, "register"):
            texture_pro.register()
            _safe_probe("texture_pro")
        else:
            print("[EchoGraph] Texture Pro module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Texture Pro plugin import failed:", e)

    # 8.85) Instance
    try:
        from nodes import instance
        if hasattr(instance, "register"):
            instance.register()
            _safe_probe("instance")
        else:
            print("[EchoGraph] Instance module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Instance plugin import failed:", e)

    # 8.9) Texture Layer
    try:
        from nodes import texture_layer
        if hasattr(texture_layer, "register"):
            texture_layer.register()
            _safe_probe("texture_layer")
        else:
            print("[EchoGraph] Texture Layer module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Texture Layer plugin import failed:", e)

    # 8.92) Material
    try:
        from nodes import material
        if hasattr(material, "register"):
            material.register()
            _safe_probe("material")
        else:
            print("[EchoGraph] Material module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Material plugin import failed:", e)

    # 8.95) Volume Selector
    try:
        from nodes import volume_selector
        if hasattr(volume_selector, "register"):
            volume_selector.register()
            _safe_probe("volume_selector")
        else:
            print("[EchoGraph] Volume Selector module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Volume Selector plugin import failed:", e)

    # 8.96) Transforms
    try:
        from nodes import transforms
        if hasattr(transforms, "register"):
            transforms.register()
            _safe_probe("transforms")
        else:
            print("[EchoGraph] Transforms module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Transforms plugin import failed:", e)

    # 8.97) FX
    try:
        from nodes import fx
        if hasattr(fx, "register"):
            fx.register()
            _safe_probe("fx")
        else:
            print("[EchoGraph] FX module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] FX plugin import failed:", e)

    # 8.98) Wire
    try:
        from nodes import wire
        if hasattr(wire, "register"):
            wire.register()
            _safe_probe("wire")
        else:
            print("[EchoGraph] Wire module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Wire plugin import failed:", e)

    # 8.99) PLY Sequence
    try:
        from nodes import ply_sequence
        if hasattr(ply_sequence, "register"):
            ply_sequence.register()
            _safe_probe("ply_sequence")
        else:
            print("[EchoGraph] PLY Sequence module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] PLY Sequence plugin import failed:", e)

    # 9) GPT Prompt
    try:
        from nodes import gpt_prompt
        if hasattr(gpt_prompt, "register"):
            gpt_prompt.register()
            _safe_probe("llm_prompt")
        else:
            print("[EchoGraph] GPT Prompt module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] GPT Prompt plugin import failed:", e)

    # 10) Chatbot
    try:
        from nodes import chatbot
        if hasattr(chatbot, "register"):
            chatbot.register()
            _safe_probe("chatbot")
        else:
            print("[EchoGraph] Chatbot module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Chatbot plugin import failed:", e)

    # 11) Database
    try:
        from nodes import database
        if hasattr(database, "register"):
            database.register()
            _safe_probe("database")
        else:
            print("[EchoGraph] Database module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Database plugin import failed:", e)

    # 12) Image Collection
    try:
        from nodes import image_collection
        if hasattr(image_collection, "register"):
            image_collection.register()
            _safe_probe("image_collection")
        else:
            print("[EchoGraph] Image Collection module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Image Collection plugin import failed:", e)

    # 13) Scene Assembly
    try:
        from nodes import scene as scene_node
        if hasattr(scene_node, "register"):
            scene_node.register()
            _safe_probe("scene")
        else:
            print("[EchoGraph] Scene module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Scene plugin import failed:", e)

    # 13.5) Camera
    try:
        from nodes import camera as camera_node
        if hasattr(camera_node, "register"):
            camera_node.register()
            _safe_probe("camera")
        else:
            print("[EchoGraph] Camera module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Camera plugin import failed:", e)

    # 14) Export FBX
    try:
        from nodes import export_fbx
        if hasattr(export_fbx, "register"):
            export_fbx.register()
            _safe_probe("export_fbx")
        else:
            print("[EchoGraph] Export FBX module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Export FBX plugin import failed:", e)

    # 14.1) Export OBJ
    try:
        from nodes import export_obj
        if hasattr(export_obj, "register"):
            export_obj.register()
            _safe_probe("export_obj")
        else:
            print("[EchoGraph] Export OBJ module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Export OBJ plugin import failed:", e)

    # 15) Render Sequence
    try:
        from nodes import render as render_node
        if hasattr(render_node, "register"):
            render_node.register()
            _safe_probe("render")
        else:
            print("[EchoGraph] Render module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Render plugin import failed:", e)

    # 16) Video Player
    try:
        from nodes import video_player as video_player_node
        if hasattr(video_player_node, "register"):
            video_player_node.register()
            _safe_probe("video_player")
        else:
            print("[EchoGraph] Video Player module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Video Player plugin import failed:", e)
