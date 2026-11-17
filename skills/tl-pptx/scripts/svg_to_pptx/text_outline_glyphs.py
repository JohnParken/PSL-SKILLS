"""Glyph records, drawing callbacks and cluster helpers split from upstream."""
from __future__ import annotations
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .drawingml.paths import PathCommand

@dataclass(frozen=True)
class _FontFace:
    path: Path
    index: int
    families: tuple[str, ...]
    weight: int
    italic: bool
    weight_range: tuple[float, float] | None
    italic_range: tuple[float, float] | None
    coverage: frozenset[int]
    order: int



@dataclass(frozen=True)
class _TextStyle:
    families: tuple[str, ...]
    font_size: float
    font_weight: int
    italic: bool
    anchor: str
    letter_spacing: float



@dataclass(frozen=True)
class _FontRun:
    face: _FontFace
    clusters: tuple[str, ...]
    global_cluster_start: int

    @property
    def text(self) -> str:
        return "".join(self.clusters)



@dataclass
class _ShapedRun:
    run: _FontRun
    font: Any
    infos: Sequence[Any]
    positions: Sequence[Any]
    scale: float
    advance_x: float



@dataclass
class _DrawState:
    origin_x: float
    origin_y: float
    scale: float
    commands: list[PathCommand]
    segment_count: int = 0

    def point(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.origin_x + x * self.scale,
            self.origin_y - y * self.scale,
        )



def _draw_functions(hb: Any) -> Any:
    functions = hb.DrawFuncs()

    def move_to(x: float, y: float, state: _DrawState) -> None:
        point = state.point(x, y)
        state.commands.append(PathCommand("M", [*point]))

    def line_to(x: float, y: float, state: _DrawState) -> None:
        point = state.point(x, y)
        state.commands.append(PathCommand("L", [*point]))
        state.segment_count += 1

    def quadratic_to(
        control_x: float,
        control_y: float,
        x: float,
        y: float,
        state: _DrawState,
    ) -> None:
        control = state.point(control_x, control_y)
        point = state.point(x, y)
        state.commands.append(PathCommand(
            "Q",
            [control[0], control[1], point[0], point[1]],
        ))
        state.segment_count += 1

    def cubic_to(
        control_1_x: float,
        control_1_y: float,
        control_2_x: float,
        control_2_y: float,
        x: float,
        y: float,
        state: _DrawState,
    ) -> None:
        control_1 = state.point(control_1_x, control_1_y)
        control_2 = state.point(control_2_x, control_2_y)
        point = state.point(x, y)
        state.commands.append(PathCommand(
            "C",
            [
                control_1[0],
                control_1[1],
                control_2[0],
                control_2[1],
                point[0],
                point[1],
            ],
        ))
        state.segment_count += 1

    def close_path(state: _DrawState) -> None:
        state.commands.append(PathCommand("Z", []))

    functions.set_move_to_func(move_to)
    functions.set_line_to_func(line_to)
    functions.set_quadratic_to_func(quadratic_to)
    functions.set_cubic_to_func(cubic_to)
    functions.set_close_path_func(close_path)
    return functions



def _cluster_offsets(clusters: Sequence[str]) -> list[int]:
    offsets: list[int] = []
    offset = 0
    for cluster in clusters:
        offsets.append(offset)
        offset += len(cluster)
    return offsets



def _required_codepoints(cluster: str) -> frozenset[int]:
    return frozenset(
        ord(character)
        for character in cluster
        if not _is_default_ignorable(character)
    )



def _is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) == "Cf"
        or 0x180B <= codepoint <= 0x180D
        or 0x2061 <= codepoint <= 0x206F
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )



def _cluster_may_have_no_outline(cluster: str) -> bool:
    return all(
        character.isspace() or _is_default_ignorable(character)
        for character in cluster
    )



def _family_key(value: str) -> str:
    return " ".join(value.split()).casefold()
