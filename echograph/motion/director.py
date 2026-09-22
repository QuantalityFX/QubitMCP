"""Small explicit gesture vocabulary; unsupported actions need a hand description."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re


@dataclass(frozen=True)
class HandIntent:
    prompt: str
    side: str = "right"
    start: float = 0.0
    end: float = 1.0
    blend: float = 0.15

    def validate(self, clip_end: float):
        if not self.prompt.strip() or self.side not in ("left", "right", "both"):
            raise ValueError("Choose a left/right/both hand gesture with a description.")
        if not all(math.isfinite(v) for v in (self.start, self.end, self.blend)):
            raise ValueError("Hand event times must be finite.")
        if not 0 <= self.start < self.end <= clip_end + 1e-7:
            raise ValueError(f"Hand interval must fit inside the clip (0–{clip_end:g} seconds).")
        if not 0 <= self.blend <= (self.end - self.start) / 2:
            raise ValueError("Hand blend must fit twice within the gesture interval.")


def direct_hands(prompt: str, clip_end: float, *, side="auto", description="", start=0.0,
                 end=None, blend=0.15) -> HandIntent:
    words = prompt.lower()
    if side == "auto":
        side_words = description.lower() if description.strip() else words
        left, right = (bool(re.search(rf"\b{hand}\s+(hand|arm|fist|index|thumb|fingers)\b", side_words))
                       for hand in ("left", "right"))
        both = re.search(r"\b(both|two)\s+(hands|arms|fists)\b", side_words)
        side = "both" if both or (left and right) else ("left" if left else "right")
    subject = "Each hand" if side == "both" else f"The {side} hand"
    if not description.strip():
        recipes = (
            (r"\b(point|points|pointing)\b", "has the index finger extended, the middle, ring and little fingers curled into the palm, and the thumb relaxed"),
            (r"\b(thumbs?[- ]?up)\b", "forms a fist with the thumb extended upward"),
            (r"\b(peace|victory)\b", "extends and separates the index and middle fingers, curling the ring and little fingers into the palm"),
            (r"\b(fist|punch|jab)\b", "curls the fingers into a closed fist with the thumb resting outside the curled fingers"),
            (r"\b(wave|waves|waving|open palm|open hand)\b", "extends all fingers and holds the palm open"),
        )
        recipe = next((text for pattern, text in recipes if re.search(pattern, words)), None)
        if recipe is None:
            raise ValueError("Automatic hands support pointing, fist/jab, open palm/wave, peace and thumbs-up. Enter a hand description for another gesture.")
        description = f"{subject} {recipe}."
    end = clip_end if end is None else end
    intent = HandIntent(description.strip(), side, start, end, min(blend, (end - start) / 2))
    intent.validate(clip_end)
    return intent
