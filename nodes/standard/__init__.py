# nodes/standard/__init__.py
from __future__ import annotations
from nodes.core import register

def register():
    # This just makes the pipeline real for the base "node" kind.
    # You can expand later (e.g., augment_infocard_footer) if needed.
    register("node", stripe_color="#64748b")
