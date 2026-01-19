from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional


DrawFn = Callable[[object, "MGLSceneItem", object], None]


@dataclass
class MGLSceneItem:
    name: str
    draw_fn: DrawFn
    payload: dict = field(default_factory=dict)
    resources: List[object] = field(default_factory=list)
    visible: bool = True
    order: int = 0
    tag: Optional[str] = None
    item_id: int = 0

    def draw(self, renderer: object, mvp: object) -> None:
        self.draw_fn(renderer, self, mvp)

    def release(self) -> None:
        for res in self.resources:
            if res is not None and hasattr(res, "release"):
                try:
                    res.release()
                except Exception:
                    pass


class MGLScene:
    def __init__(self) -> None:
        self._items: List[MGLSceneItem] = []
        self._next_id = 1

    def add(self, item: MGLSceneItem) -> int:
        item.item_id = self._next_id
        self._next_id += 1
        self._items.append(item)
        return item.item_id

    def clear(self) -> None:
        for item in self._items:
            item.release()
        self._items = []

    def items(self) -> List[MGLSceneItem]:
        return list(self._items)

    def iter_by_tag(self, tag: str) -> Iterable[MGLSceneItem]:
        for item in self._items:
            if item.tag == tag:
                yield item

    def has_tag(self, tag: str) -> bool:
        return any(item.tag == tag for item in self._items)

    def remove_by_tag(self, tag: str) -> None:
        kept: List[MGLSceneItem] = []
        for item in self._items:
            if item.tag == tag:
                item.release()
            else:
                kept.append(item)
        self._items = kept

    def set_visible_by_tag(self, tag: str, visible: bool) -> None:
        for item in self._items:
            if item.tag == tag:
                item.visible = visible

    def draw(self, renderer: object, mvp: object) -> None:
        for item in sorted(self._items, key=lambda it: it.order):
            if item.visible:
                item.draw(renderer, mvp)
