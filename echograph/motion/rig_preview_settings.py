"""Preview Rig's own category in the application's settings document."""
import json
import os
import tempfile
from pathlib import Path

from .conversion import JOINT_NAMES
from .rig_limits import PREVIEW_JOINT_LIMITS, preview_limit_fields, preview_limit_from_fields

APP_SETTINGS_PATH = Path(__file__).resolve().parents[2]/'app_settings.json'
SETTINGS_CATEGORY = 'preview_rig'


def default_preview_limits():
    return {joint:preview_limit_from_fields(joint,{}) for joint in PREVIEW_JOINT_LIMITS}


def load_preview_limits(path=None):
    limits = default_preview_limits()
    try:
        data = json.loads(Path(path or APP_SETTINGS_PATH).read_text(encoding='utf-8'))
        saved = data[SETTINGS_CATEGORY]['joint_limits']
        if not isinstance(saved,dict):
            return limits
        for joint in limits:
            fields = saved.get(JOINT_NAMES[joint])
            if isinstance(fields,dict):
                limits[joint] = preview_limit_from_fields(joint,fields)
    except (OSError,ValueError,KeyError,TypeError):
        pass
    return limits


def load_preview_volumes(path=None):
    from .rig_volumes import volume_settings
    try:
        data = json.loads(Path(path or APP_SETTINGS_PATH).read_text(encoding='utf-8'))
        saved = data[SETTINGS_CATEGORY].get('volumes', {})
        return volume_settings(saved if isinstance(saved, dict) else {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return volume_settings()


def load_preview_hand_limits(path=None):
    from .hand_rig import hand_limits_from_fields
    try:
        data = json.loads(Path(path or APP_SETTINGS_PATH).read_text(encoding='utf-8'))
        return hand_limits_from_fields(data[SETTINGS_CATEGORY].get('joint_limits',{}))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return hand_limits_from_fields()


def save_preview_limits(limits, path=None, *, volumes=None, names=JOINT_NAMES):
    path = Path(path or APP_SETTINGS_PATH)
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        settings = {}
    if not isinstance(settings,dict):
        raise ValueError('Application settings must be a JSON object.')
    category = settings.get(SETTINGS_CATEGORY)
    category = dict(category) if isinstance(category, dict) else {}
    from .hand_rig import named_limit_default
    saved = category.get('joint_limits')
    saved = dict(saved) if isinstance(saved,dict) else {}
    for j,limit in limits.items():
        default = named_limit_default(names[j])
        if default:
            saved[names[j]] = preview_limit_fields(j,preview_limit_from_fields(j,preview_limit_fields(j,limit),defaults=default))
    category.update(version=3,joint_limits=saved)
    if volumes is not None:
        from .rig_volumes import volume_settings
        category['volumes'] = volume_settings(volumes)
    settings[SETTINGS_CATEGORY] = category
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix=path.name+'.',suffix='.tmp',delete=False) as output:
            temporary = Path(output.name)
            json.dump(settings,output,ensure_ascii=False,indent=2,allow_nan=False)
            output.write('\n')
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
