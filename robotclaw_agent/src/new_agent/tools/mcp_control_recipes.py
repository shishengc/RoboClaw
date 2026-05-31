"""Central recipe parameters for mcp_control AprilTag pick-and-place tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TagPickPlaceRecipe:
    name: str
    description: str
    source_base_offset_m: tuple[float, float, float]
    place_base_offset_m: tuple[float, float, float]
    approach_distance_m: float
    lift_height_m: float
    hover_height_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_name": self.name,
            "description": self.description,
            "source_base_offset_m": list(self.source_base_offset_m),
            "place_base_offset_m": list(self.place_base_offset_m),
            "approach_distance_m": self.approach_distance_m,
            "lift_height_m": self.lift_height_m,
            "hover_height_m": self.hover_height_m,
        }


TAG_PICK_PLACE_RECIPES: dict[str, TagPickPlaceRecipe] = {
    "assembly_on": TagPickPlaceRecipe(
        name="assembly_on",
        description="Place tag 0 on tag 1.",
        source_base_offset_m=(-0.01, 0.01, -0.10),
        place_base_offset_m=(-0.012, -0.015, 0.02),
        approach_distance_m=0.06,
        lift_height_m=0.10,
        hover_height_m=0.10,
    ),
    "inside": TagPickPlaceRecipe(
        name="inside",
        description="Place tag 2 inside tag 3.",
        source_base_offset_m=(0.0, 0.0, -0.06),
        place_base_offset_m=(0.00, 0.00, 0.10),
        approach_distance_m=0.06,
        lift_height_m=0.10,
        hover_height_m=0.10,
    ),
    "inside_tag0_tag4": TagPickPlaceRecipe(
        name="inside_tag0_tag4",
        description="Place tag 0 inside tag 4.",
        source_base_offset_m=(-0.01, 0.01, -0.07),
        place_base_offset_m=(0.00, 0.00, 0.10),
        approach_distance_m=0.06,
        lift_height_m=0.10,
        hover_height_m=0.10,
    ),
}

DEFAULT_TAG_PICK_PLACE_RECIPE = "assembly_on"

RELATION_TO_RECIPE: dict[str, str] = {
    "on": "assembly_on",
    "onto": "assembly_on",
    "assembly": "assembly_on",
    "inside": "inside",
    "in": "inside",
    "sorting": "inside",
}

TAG_PAIR_TO_RECIPE: dict[tuple[int, int], str] = {
    (0, 1): "assembly_on",
    (2, 3): "inside",
    (0, 4): "inside_tag0_tag4",
}


def get_tag_pick_place_recipe(
    recipe_name: str | None = None,
    *,
    relation: str | None = None,
    source_tag_id: int | None = None,
    destination_tag_id: int | None = None,
) -> TagPickPlaceRecipe:
    """Return the canonical recipe for a task relation or explicit name."""
    tag_pair_recipe = None
    if source_tag_id is not None and destination_tag_id is not None:
        tag_pair_recipe = TAG_PAIR_TO_RECIPE.get((int(source_tag_id), int(destination_tag_id)))

    selected = tag_pair_recipe or recipe_name or RELATION_TO_RECIPE.get((relation or "").strip().lower())
    selected = selected or DEFAULT_TAG_PICK_PLACE_RECIPE
    try:
        return TAG_PICK_PLACE_RECIPES[selected]
    except KeyError as exc:
        valid = sorted(TAG_PICK_PLACE_RECIPES)
        raise ValueError(f"unknown recipe {selected!r}; valid recipes: {valid}") from exc


def recipe_names() -> list[str]:
    return sorted(TAG_PICK_PLACE_RECIPES)
