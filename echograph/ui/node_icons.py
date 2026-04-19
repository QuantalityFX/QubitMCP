# echograph/ui/node_icons.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from echograph.qt_compat import QtGui

# Optional icons (pixmaps)
_DB_ICON = None
_LLM_ICON = None
_LLM_SERVER_ICON = None
_APPEND_ICON = None
_NOTE_ICON = None
_LIBRARIAN_ICON = None
_IMPORT_ICON = None
_FBX_ICON = None
_GLB_ICON = None
_OBJ_ICON = None
_PLT_ICON = None
_SCENE_ICON = None
_SWITCH_ICON = None
_CHATBOT_ICON = None
_OUTPUT_ICON = None
_PYTHON_ICON = None
_SCREENGRAB_ICON = None
_RENDER_NODE_ICON = None
_VIDEO_PLAYER_ICON = None
_INSTANCE_ICON = None
_PRIMITIVE_ICON = None
_HTML_PREVIEW_ICON = None
_IMAGE_COLLECTION_ICON = None
_TEXTURE_LAYER_ICON = None
_TEXTURE_NODE_ICON = None
_MATERIAL_NODE_ICON = None
_UV_UNWRAP_ICON = None
_TRANSFORMS_ICON = None
_GANTT_ICON = None
_KEYBOARD_SEQUENCE_ICON = None
_QUBIT_DECK_CONTROLLER_ICON = None
_FX_NODE_ICON = None
_CAMERA_NODE_ICON = None
_VOLUME_SPLIT_ICON = None
_VOICE_ACTOR_ICON = None
_MEDIGATOR_ICON = None


def _load_pm(rel_icon_name: str):
    try:
        icon_path = Path(__file__).resolve().parents[2] / "icons" / rel_icon_name
        if icon_path.is_file():
            pm = QtGui.QPixmap(str(icon_path))
            if not pm.isNull():
                return pm
    except Exception:
        pass
    return None


def _db_icon():
    global _DB_ICON
    if _DB_ICON is not None:
        return _DB_ICON
    _DB_ICON = _load_pm("Database_Icon.png")
    return _DB_ICON


def _llm_icon():
    global _LLM_ICON
    if _LLM_ICON is not None:
        return _LLM_ICON
    _LLM_ICON = _load_pm("LLM_Icon.png")
    return _LLM_ICON


def _llm_server_icon():
    global _LLM_SERVER_ICON
    if _LLM_SERVER_ICON is not None:
        return _LLM_SERVER_ICON
    _LLM_SERVER_ICON = _load_pm("Server_Icon.png")
    return _LLM_SERVER_ICON


def _append_icon():
    global _APPEND_ICON
    if _APPEND_ICON is not None:
        return _APPEND_ICON
    _APPEND_ICON = _load_pm("append_icon.png")
    return _APPEND_ICON


def _note_icon():
    global _NOTE_ICON
    if _NOTE_ICON is not None:
        return _NOTE_ICON
    _NOTE_ICON = _load_pm("Electric_Pen_Icon.png")
    return _NOTE_ICON


def _librarian_icon():
    global _LIBRARIAN_ICON
    if _LIBRARIAN_ICON is not None:
        return _LIBRARIAN_ICON
    _LIBRARIAN_ICON = _load_pm("librarian_search_Icon.png")
    return _LIBRARIAN_ICON


def _import_icon():
    global _IMPORT_ICON
    if _IMPORT_ICON is not None:
        return _IMPORT_ICON
    _IMPORT_ICON = _load_pm("Import_File_Icon.png")
    return _IMPORT_ICON


def _fbx_icon():
    global _FBX_ICON
    if _FBX_ICON is not None:
        return _FBX_ICON
    _FBX_ICON = _load_pm("FBX_Icon.png")
    return _FBX_ICON


def _glb_icon():
    global _GLB_ICON
    if _GLB_ICON is not None:
        return _GLB_ICON
    _GLB_ICON = _load_pm("GLB_Icon.png")
    return _GLB_ICON


def _obj_icon():
    global _OBJ_ICON
    if _OBJ_ICON is not None:
        return _OBJ_ICON
    _OBJ_ICON = _load_pm("OBJ_Icon.png")
    return _OBJ_ICON


def _ply_icon():
    global _PLT_ICON
    if _PLT_ICON is not None:
        return _PLT_ICON
    _PLT_ICON = _load_pm("PLT_Icon.png")
    return _PLT_ICON


def _scene_icon():
    global _SCENE_ICON
    if _SCENE_ICON is not None:
        return _SCENE_ICON
    _SCENE_ICON = _load_pm("Scene_Icon.png")
    return _SCENE_ICON


def _switch_icon():
    global _SWITCH_ICON
    if _SWITCH_ICON is not None:
        return _SWITCH_ICON
    _SWITCH_ICON = _load_pm("Switch_Icon.png")
    return _SWITCH_ICON


def _chatbot_icon():
    global _CHATBOT_ICON
    if _CHATBOT_ICON is not None:
        return _CHATBOT_ICON
    _CHATBOT_ICON = _load_pm("Chatbot_Icon.png")
    return _CHATBOT_ICON


def _output_icon():
    global _OUTPUT_ICON
    if _OUTPUT_ICON is not None:
        return _OUTPUT_ICON
    _OUTPUT_ICON = _load_pm("Out_Node_Icon.png")
    return _OUTPUT_ICON


def _python_icon():
    global _PYTHON_ICON
    if _PYTHON_ICON is not None:
        return _PYTHON_ICON
    _PYTHON_ICON = _load_pm("Python_Icon.png")
    return _PYTHON_ICON


def _screengrab_icon():
    global _SCREENGRAB_ICON
    if _SCREENGRAB_ICON is not None:
        return _SCREENGRAB_ICON
    _SCREENGRAB_ICON = _load_pm("screengrab _Icon_s_001.png")
    return _SCREENGRAB_ICON


def _render_node_icon():
    global _RENDER_NODE_ICON
    if _RENDER_NODE_ICON is not None:
        return _RENDER_NODE_ICON
    _RENDER_NODE_ICON = _load_pm("RenderNode_Icon.png")
    return _RENDER_NODE_ICON


def _video_player_icon():
    global _VIDEO_PLAYER_ICON
    if _VIDEO_PLAYER_ICON is not None:
        return _VIDEO_PLAYER_ICON
    _VIDEO_PLAYER_ICON = _load_pm("ReelVideo_Icon.png") or _load_pm("PlayButton_icon.png")
    return _VIDEO_PLAYER_ICON


def _instance_icon():
    global _INSTANCE_ICON
    if _INSTANCE_ICON is not None:
        return _INSTANCE_ICON
    _INSTANCE_ICON = _load_pm("instance_icon.png")
    return _INSTANCE_ICON


def _primitive_icon():
    global _PRIMITIVE_ICON
    if _PRIMITIVE_ICON is not None:
        return _PRIMITIVE_ICON
    _PRIMITIVE_ICON = _load_pm("Primitive_Icon.png")
    return _PRIMITIVE_ICON


def _html_preview_icon():
    global _HTML_PREVIEW_ICON
    if _HTML_PREVIEW_ICON is not None:
        return _HTML_PREVIEW_ICON
    _HTML_PREVIEW_ICON = _load_pm("HTML_Preview.png") or _load_pm("explorer_button_icon.png")
    return _HTML_PREVIEW_ICON


def _image_collection_icon():
    global _IMAGE_COLLECTION_ICON
    if _IMAGE_COLLECTION_ICON is not None:
        return _IMAGE_COLLECTION_ICON
    _IMAGE_COLLECTION_ICON = _load_pm("PhotoCollection.png")
    return _IMAGE_COLLECTION_ICON


def _camera_node_icon():
    global _CAMERA_NODE_ICON
    if _CAMERA_NODE_ICON is not None:
        return _CAMERA_NODE_ICON
    _CAMERA_NODE_ICON = (
        _load_pm("Camera_icon.png")
        or _load_pm("Camera _Icon.png")
        or _load_pm("CameraCtrl_On_Icon.png")
    )
    return _CAMERA_NODE_ICON


def _volume_split_icon():
    global _VOLUME_SPLIT_ICON
    if _VOLUME_SPLIT_ICON is not None:
        return _VOLUME_SPLIT_ICON
    _VOLUME_SPLIT_ICON = _load_pm("volume_split_icon.png") or _load_pm("grid_on_icon.png")
    return _VOLUME_SPLIT_ICON


def _texture_layer_icon():
    global _TEXTURE_LAYER_ICON
    if _TEXTURE_LAYER_ICON is not None:
        return _TEXTURE_LAYER_ICON
    _TEXTURE_LAYER_ICON = _load_pm("layer_Texture_Icon.png")
    return _TEXTURE_LAYER_ICON


def _texture_node_icon():
    global _TEXTURE_NODE_ICON
    if _TEXTURE_NODE_ICON is not None:
        return _TEXTURE_NODE_ICON
    _TEXTURE_NODE_ICON = _load_pm("TextureNode_Icon.png")
    return _TEXTURE_NODE_ICON


def _material_node_icon():
    global _MATERIAL_NODE_ICON
    if _MATERIAL_NODE_ICON is not None:
        return _MATERIAL_NODE_ICON
    _MATERIAL_NODE_ICON = _load_pm("MaterialGenerator_Icon_M.png") or _load_pm("LiveMaterial_Icon.png")
    return _MATERIAL_NODE_ICON


def _uv_unwrap_icon():
    global _UV_UNWRAP_ICON
    if _UV_UNWRAP_ICON is not None:
        return _UV_UNWRAP_ICON
    _UV_UNWRAP_ICON = _load_pm("UvUnwrapNode_Icon.png")
    return _UV_UNWRAP_ICON


def _transforms_icon():
    global _TRANSFORMS_ICON
    if _TRANSFORMS_ICON is not None:
        return _TRANSFORMS_ICON
    _TRANSFORMS_ICON = _load_pm("TranformNode_Icon.png") or _load_pm("WorldGizmoOn_Icon.png")
    return _TRANSFORMS_ICON


def _gantt_icon():
    global _GANTT_ICON
    if _GANTT_ICON is not None:
        return _GANTT_ICON
    _GANTT_ICON = _load_pm("Calendar_Icon.png")
    return _GANTT_ICON


def _keyboard_sequence_icon():
    global _KEYBOARD_SEQUENCE_ICON
    if _KEYBOARD_SEQUENCE_ICON is not None:
        return _KEYBOARD_SEQUENCE_ICON
    _KEYBOARD_SEQUENCE_ICON = _load_pm("keyframe_Icon.png") or _load_pm("CalendarToday_Icon.png")
    return _KEYBOARD_SEQUENCE_ICON


def _qubit_deck_controller_icon():
    global _QUBIT_DECK_CONTROLLER_ICON
    if _QUBIT_DECK_CONTROLLER_ICON is not None:
        return _QUBIT_DECK_CONTROLLER_ICON
    try:
        icon_path = Path(__file__).resolve().parents[2] / "nodes" / "qubit_deck_controller" / "QBeam__API_Icon.ico"
        if icon_path.is_file():
            pm = QtGui.QPixmap(str(icon_path))
            if not pm.isNull():
                _QUBIT_DECK_CONTROLLER_ICON = pm
                return _QUBIT_DECK_CONTROLLER_ICON
    except Exception:
        pass
    _QUBIT_DECK_CONTROLLER_ICON = _load_pm("debug_002_Icon_s.png") or _load_pm("Server_Icon.png")
    return _QUBIT_DECK_CONTROLLER_ICON


def _fx_node_icon():
    global _FX_NODE_ICON
    if _FX_NODE_ICON is not None:
        return _FX_NODE_ICON
    _FX_NODE_ICON = _load_pm("FX_node_icon.png")
    return _FX_NODE_ICON


def _voice_actor_icon():
    global _VOICE_ACTOR_ICON
    if _VOICE_ACTOR_ICON is not None:
        return _VOICE_ACTOR_ICON
    _VOICE_ACTOR_ICON = _load_pm("TanyaAI_Icon.png")
    return _VOICE_ACTOR_ICON


def _medigator_icon():
    global _MEDIGATOR_ICON
    if _MEDIGATOR_ICON is not None:
        return _MEDIGATOR_ICON
    _MEDIGATOR_ICON = _load_pm("Python_Icon.png") or _load_pm("Server_Icon.png")
    return _MEDIGATOR_ICON
