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

    # 4.6) Keyboard Sequence
    try:
        from nodes import keyboard_sequence
        if hasattr(keyboard_sequence, "register"):
            keyboard_sequence.register()
            _safe_probe("keyboard_sequence")
        else:
            print("[EchoGraph] Keyboard Sequence module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Keyboard Sequence plugin import failed:", e)

    # 4.7) Serial COM
    try:
        from nodes import serial_com
        if hasattr(serial_com, "register"):
            serial_com.register()
            _safe_probe("serial_com")
        else:
            print("[EchoGraph] Serial COM module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Serial COM plugin import failed:", e)

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

    # 8.52) Curve primitive
    try:
        from nodes import curve
        if hasattr(curve, "register"):
            curve.register()
            _safe_probe("curve")
        else:
            print("[EchoGraph] Curve module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Curve plugin import failed:", e)

    # 8.55) Copy To Points
    try:
        from nodes import copy_to_points
        if hasattr(copy_to_points, "register"):
            copy_to_points.register()
            _safe_probe("copy_to_points")
        else:
            print("[EchoGraph] Copy To Points module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Copy To Points plugin import failed:", e)

    # 8.57) Modeler
    try:
        from nodes import modeler
        if hasattr(modeler, "register"):
            modeler.register()
            _safe_probe("modeler")
        else:
            print("[EchoGraph] Modeler module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Modeler plugin import failed:", e)

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

    # 8.91) Mask
    try:
        from nodes import mask
        if hasattr(mask, "register"):
            mask.register()
            _safe_probe("mask")
        else:
            print("[EchoGraph] Mask module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Mask plugin import failed:", e)

    # 8.915) Groom Guides
    try:
        from nodes import groom_guides
        if hasattr(groom_guides, "register"):
            groom_guides.register()
            _safe_probe("groom_guides")
        else:
            print("[EchoGraph] Groom Guides module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Groom Guides plugin import failed:", e)

    # 8.916) Groom Deform
    try:
        from nodes import groom_deform
        if hasattr(groom_deform, "register"):
            groom_deform.register()
            _safe_probe("groom_deform")
        else:
            print("[EchoGraph] Groom Deform module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Groom Deform plugin import failed:", e)

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

    # 11.5) Voice Actor
    try:
        from nodes import voice_actor
        if hasattr(voice_actor, "register"):
            voice_actor.register()
            _safe_probe("voice_actor")
        else:
            print("[EchoGraph] Voice Actor module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Voice Actor plugin import failed:", e)

    # 11.6) Mediator Agent
    try:
        from nodes import mediator_agent
        if hasattr(mediator_agent, "register"):
            mediator_agent.register()
            _safe_probe("mediator_agent")
        else:
            print("[EchoGraph] Mediator Agent module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Mediator Agent plugin import failed:", e)

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

    # 13.6) Light
    try:
        from nodes import light as light_node
        if hasattr(light_node, "register"):
            light_node.register()
            _safe_probe("light")
        else:
            print("[EchoGraph] Light module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Light plugin import failed:", e)

    # 13.7) FBX Import
    try:
        from nodes import fbx_import
        if hasattr(fbx_import, "register"):
            fbx_import.register()
            _safe_probe("fbx_import")
        else:
            print("[EchoGraph] FBX Import module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] FBX Import plugin import failed:", e)

    # 13.8) Mocap Import
    try:
        from nodes import mocap_import
        if hasattr(mocap_import, "register"):
            mocap_import.register()
            _safe_probe("mocap_import")
        else:
            print("[EchoGraph] Mocap Import module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Mocap Import plugin import failed:", e)

    # 13.85) GEN-X Video Mocap
    try:
        from nodes import genx_video_mocap
        if hasattr(genx_video_mocap, "register"):
            genx_video_mocap.register()
            _safe_probe("GEN-X-VideoMocap")
        else:
            print("[EchoGraph] GEN-X Video Mocap module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] GEN-X Video Mocap plugin import failed:", e)

    # 13.9) Anim Retarget
    try:
        from nodes import anim_retarget
        if hasattr(anim_retarget, "register"):
            anim_retarget.register()
            _safe_probe("anim_retarget")
        else:
            print("[EchoGraph] Anim Retarget module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Anim Retarget plugin import failed:", e)

    # 13.95) Skinned Splat Proxy
    try:
        from nodes import skinned_splat_proxy
        if hasattr(skinned_splat_proxy, "register"):
            skinned_splat_proxy.register()
            _safe_probe("skinned_splat_proxy")
        else:
            print("[EchoGraph] Skinned Splat Proxy module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Skinned Splat Proxy plugin import failed:", e)

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

    # 16.1) Post Process
    try:
        from nodes import post_process
        if hasattr(post_process, "register"):
            post_process.register()
            _safe_probe("post_process")
        else:
            print("[EchoGraph] Post Process module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Post Process plugin import failed:", e)

    # 16.2) Sequence to MP4
    try:
        from nodes import sequence_to_mp4
        if hasattr(sequence_to_mp4, "register"):
            sequence_to_mp4.register()
            _safe_probe("sequence_to_mp4")
        else:
            print("[EchoGraph] Sequence to MP4 module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Sequence to MP4 plugin import failed:", e)

    # 16.5) Qubit Deck Controller
    try:
        from nodes import qubit_deck_controller
        if hasattr(qubit_deck_controller, "register"):
            qubit_deck_controller.register()
            _safe_probe("qubit_deck_controller")
        else:
            print("[EchoGraph] Qubit Deck Controller module has no 'register' function.")
    except Exception as e:
        print("[EchoGraph] Qubit Deck Controller plugin import failed:", e)

