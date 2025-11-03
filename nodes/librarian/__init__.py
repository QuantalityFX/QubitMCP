# nodes/librarian/__init__.py
from nodes.core import register_spec
from .spec import LIBRARIAN_SPEC

def register():
    register_spec("librarian", LIBRARIAN_SPEC)
