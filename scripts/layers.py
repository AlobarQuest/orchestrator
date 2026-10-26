"""Tracked file -> layer, as a function of `layers.toml`.

The orchestrator is worked on one layer at a time: the twelve units of ownership on the SDS layer
map, plus tooling and config, and project records. `layers.toml` says which files make up each
layer, by path pattern. This module reads it and answers two questions: which files belong to a
layer, and which tracked files belong to no layer or to more than one.

The second question is what keeps the map true. `tests/architecture/test_layer_manifest.py`
fails on either answer being non-empty, so a new file must be placed when it is added rather than
discovered later in no layer's file list.
"""

import argparse
import subprocess
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "layers.toml"


@dataclass(frozen=True)
class Layer:
    id: str
    name: str
    description: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Assignment:
    layers: Mapping[str, tuple[str, ...]]
    unassigned: tuple[str, ...]
    overlapping: Mapping[str, tuple[str, ...]]


def load(manifest: Path = MANIFEST) -> dict[str, Layer]:
    with manifest.open("rb") as handle:
        table = tomllib.load(handle)
    return {
        layer_id: Layer(layer_id, spec["name"], spec["description"], tuple(spec["paths"]))
        for layer_id, spec in table.items()
    }


def tracked_files(root: Path = ROOT) -> list[str]:
    """Files git tracks. Untracked ones are left out, so local clutter can't redden the guard; a
    new file is placed when it is added, or CI fails on it."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    return [path for path in listing.stdout.split("\0") if path]


def assign(files: Iterable[str], layers: Mapping[str, Layer]) -> Assignment:
    """Place each file in every layer one of whose patterns matches it."""
    owners: dict[str, list[str]] = {}
    for path in files:
        pure = PurePosixPath(path)
        owners[path] = [
            layer.id
            for layer in layers.values()
            if any(pure.full_match(pattern) for pattern in layer.paths)
        ]
    return Assignment(
        layers={
            layer_id: tuple(path for path, ids in owners.items() if ids == [layer_id])
            for layer_id in layers
        },
        unassigned=tuple(path for path, ids in owners.items() if not ids),
        overlapping={path: tuple(ids) for path, ids in owners.items() if len(ids) > 1},
    )


def unused_patterns(files: Iterable[str], layers: Mapping[str, Layer]) -> list[tuple[str, str]]:
    """Patterns that match no tracked file: a moved or deleted path the manifest still names."""
    pures = [PurePosixPath(path) for path in files]
    return [
        (layer.id, pattern)
        for layer in layers.values()
        for pattern in layer.paths
        if not any(pure.full_match(pattern) for pure in pures)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List a layer's files, or count every layer's.")
    parser.add_argument("layer", nargs="?", help="a layer id from layers.toml; omit for counts")
    args = parser.parse_args(argv)
    layers = load()
    assignment = assign(tracked_files(), layers)
    if args.layer is None:
        for layer in layers.values():
            print(f"{layer.id:<13} {len(assignment.layers[layer.id]):>4}  {layer.name}")
        print(f"{'unassigned':<13} {len(assignment.unassigned):>4}")
        print(f"{'overlapping':<13} {len(assignment.overlapping):>4}")
        return 0
    if args.layer not in layers:
        parser.error(f"unknown layer {args.layer!r}; choose from {', '.join(layers)}")
    print("\n".join(assignment.layers[args.layer]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
