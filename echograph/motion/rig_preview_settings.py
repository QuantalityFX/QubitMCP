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


def save_preview_limits(limits, path=None):
    path = Path(path or APP_SETTINGS_PATH)
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        settings = {}
    if not isinstance(settings,dict):
        raise ValueError('Application settings must be a JSON object.')
    settings[SETTINGS_CATEGORY] = dict(version=1,joint_limits={
        JOINT_NAMES[j]:preview_limit_fields(j,preview_limit_from_fields(j,preview_limit_fields(j,limit)))
        for j,limit in limits.items() if j in PREVIEW_JOINT_LIMITS})
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
