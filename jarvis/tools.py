"""Реестр инструментов. Инструмент = функция с аннотациями + docstring + уровень риска.

    from jarvis.tools import tool

    @tool(risk="green")
    def get_time() -> str:
        "Текущие дата и время."
        ...

Уровни риска:
  green  — выполняется сразу (чтение, поиск)
  yellow — нужно подтверждение, можно «всегда разрешать» (перемещение, запись, запуск программ)
  red    — только явное подтверждение кнопкой (удаление, shell, правка своего кода)
"""
from __future__ import annotations

import inspect
import re
import typing
from dataclasses import dataclass
from typing import Any, Callable

RISKS = ("green", "yellow", "red")

_PY2JSON = {str: "string", int: "integer", float: "number", bool: "boolean",
            list: "array", dict: "object"}


@dataclass
class Tool:
    name: str
    func: Callable[..., Any]
    description: str
    params: dict
    required: list[str]
    risk: str
    source: str = "core"        # core | skill:<file>

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name,
            "description": f"[{self.risk}] {self.description}",
            "parameters": {"type": "object", "properties": self.params, "required": self.required},
        }}


REGISTRY: dict[str, Tool] = {}
_current_source = "core"


def _json_type(ann: Any) -> dict:
    origin = typing.get_origin(ann)
    if origin in (list, typing.List):
        args = typing.get_args(ann)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin is typing.Union or str(origin) == "types.UnionType":
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return _json_type(args[0]) if args else {"type": "string"}
    if origin is typing.Literal:
        return {"type": "string", "enum": list(typing.get_args(ann))}
    return {"type": _PY2JSON.get(ann, "string")}


def _param_docs(doc: str) -> dict[str, str]:
    """Достаёт описания параметров из строк вида `name: описание` после заголовка Args."""
    out: dict[str, str] = {}
    in_args = False
    for line in doc.splitlines():
        s = line.strip()
        if s.lower().rstrip(":") in ("args", "аргументы", "параметры"):
            in_args = True
            continue
        if in_args:
            m = re.match(r"^(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$", s)
            if m:
                out[m.group(1)] = m.group(2)
            elif not s:
                in_args = False
    return out


def tool(risk: str = "green", name: str | None = None):
    if risk not in RISKS:
        raise ValueError(f"risk must be one of {RISKS}")

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        doc = inspect.getdoc(fn) or fn.__name__
        summary = doc.split("\n\n")[0].replace("\n", " ").strip()
        pdocs = _param_docs(doc)
        hints = typing.get_type_hints(fn)
        params, required = {}, []
        for p in inspect.signature(fn).parameters.values():
            if p.name.startswith("_"):
                continue
            spec = _json_type(hints.get(p.name, str))
            if p.name in pdocs:
                spec["description"] = pdocs[p.name]
            if p.default is inspect.Parameter.empty:
                required.append(p.name)
            else:
                spec.setdefault("description", "")
                spec["description"] = (spec["description"] + f" (по умолчанию {p.default!r})").strip()
            params[p.name] = spec
        t = Tool(name or fn.__name__, fn, summary, params, required, risk, _current_source)
        REGISTRY[t.name] = t
        fn.__jarvis_tool__ = t
        return fn

    return deco


def set_source(src: str) -> None:
    global _current_source
    _current_source = src


def schemas() -> list[dict]:
    return [t.schema() for t in REGISTRY.values()]


def remove_source(src: str) -> None:
    for k in [k for k, t in REGISTRY.items() if t.source == src]:
        del REGISTRY[k]
