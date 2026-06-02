"""Central recipe parameters for mcp_control AprilTag pick-and-place tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ObjectTagBinding:
    canonical_name: str
    display_name: str
    tag_id: int
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": self.canonical_name,
            "display_name": self.display_name,
            "tag_id": self.tag_id,
            "aliases": list(self.aliases),
        }


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


@dataclass(frozen=True)
class SwitchSceneRecipe:
    name: str
    description: str
    button_object: str
    arm: str
    base_offset_m: tuple[float, float, float]
    press_interval_s: float
    move_duration_s: float
    gripper_duration_s: float

    @property
    def button_tag_id(self) -> int:
        return tag_id_for_object(self.button_object)

    def to_tool_defaults(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "button_tag_id": self.button_tag_id,
            "base_offset_m": list(self.base_offset_m),
            "move_duration_s": self.move_duration_s,
            "gripper_duration_s": self.gripper_duration_s,
            "press_interval_s": self.press_interval_s,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_name": self.name,
            "description": self.description,
            "button_object": self.button_object,
            **self.to_tool_defaults(),
        }


OBJECT_TAG_BINDINGS: dict[str, ObjectTagBinding] = {
    "bearing": ObjectTagBinding(
        canonical_name="bearing",
        display_name="轴承",
        tag_id=18,
        aliases=("轴承", "bearing"),
    ),
    "base": ObjectTagBinding(
        canonical_name="base",
        display_name="底座",
        tag_id=0,
        aliases=("底座", "base"),
    ),
    "waste": ObjectTagBinding(
        canonical_name="waste",
        display_name="废品",
        tag_id=2,
        aliases=("废品", "waste", "scrap", "reject"),
    ),
    "defective_box": ObjectTagBinding(
        canonical_name="defective_box",
        display_name="次品盒",
        tag_id=3,
        aliases=("次品盒", "defective_box", "bad_box", "reject_box", "ng_box"),
    ),
    "good_box": ObjectTagBinding(
        canonical_name="good_box",
        display_name="良品盒",
        tag_id=4,
        aliases=("良品盒", "good_box", "ok_box"),
    ),
    "workpiece": ObjectTagBinding(
        canonical_name="workpiece",
        display_name="工件",
        tag_id=5,
        aliases=("工件", "workpiece", "part"),
    ),
    "drawer_magazine": ObjectTagBinding(
        canonical_name="drawer_magazine",
        display_name="抽屉式料仓",
        tag_id=6,
        aliases=("抽屉式料仓", "抽屉", "料仓", "drawer", "drawer_magazine"),
    ),
    "scene_switch_button": ObjectTagBinding(
        canonical_name="scene_switch_button",
        display_name="场景切换按钮",
        tag_id=19,
        aliases=("场景切换按钮", "切换按钮", "按钮", "scene_switch_button", "switch_button"),
    ),
}

_OBJECT_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _binding in OBJECT_TAG_BINDINGS.items():
    _OBJECT_ALIAS_TO_CANONICAL[_canonical] = _canonical
    _OBJECT_ALIAS_TO_CANONICAL[_binding.display_name] = _canonical
    for _alias in _binding.aliases:
        _OBJECT_ALIAS_TO_CANONICAL[_alias.strip().lower()] = _canonical
        _OBJECT_ALIAS_TO_CANONICAL[_alias.strip()] = _canonical


TAG_PICK_PLACE_RECIPES: dict[str, TagPickPlaceRecipe] = {
    "bearing_on_base": TagPickPlaceRecipe(
        name="bearing_on_base",
        description="Place the bearing on the base.",
        source_base_offset_m=(0.015, 0.00, -0.05),
        place_base_offset_m=(0.02, -0.01, 0.02),
        approach_distance_m=0.06,
        lift_height_m=0.10,
        hover_height_m=0.10,
    ),
    "waste_to_defective_box": TagPickPlaceRecipe(
        name="waste_to_defective_box",
        description="Place the waste item inside the defective box.",
        source_base_offset_m=(0.0, 0.0, -0.06),
        place_base_offset_m=(0.00, 0.00, 0.10),
        approach_distance_m=0.06,
        lift_height_m=0.10,
        hover_height_m=0.10,
    ),
    "bearing_to_good_box": TagPickPlaceRecipe(
        name="bearing_to_good_box",
        description="Place the bearing inside the good box.",
        source_base_offset_m=(-0.01, -0.02, -0.05),
        place_base_offset_m=(0.00, 0.00, 0.10),
        approach_distance_m=0.06,
        lift_height_m=0.10,
        hover_height_m=0.10,
    ),
}

RECIPE_ALIASES: dict[str, str] = {
    "assembly_on": "bearing_on_base",
    "inside": "waste_to_defective_box",
    "inside_tag0_tag4": "bearing_to_good_box",
}

DEFAULT_TAG_PICK_PLACE_RECIPE = "bearing_on_base"

DEFAULT_SWITCH_SCENE_RECIPE = SwitchSceneRecipe(
    name="default_scene_switch",
    description="Press the scene-switch button using the validated robot demo parameters.",
    button_object="scene_switch_button",
    arm="right",
    base_offset_m=(0.0, 0.0, -0.01),
    press_interval_s=3.0,
    move_duration_s=2.0,
    gripper_duration_s=0.5,
)

RELATION_TO_RECIPE: dict[str, str] = {
    "on": "bearing_on_base",
    "onto": "bearing_on_base",
    "assembly": "bearing_on_base",
    "inside": "waste_to_defective_box",
    "in": "waste_to_defective_box",
    "sorting": "waste_to_defective_box",
}

OBJECT_PAIR_TO_RECIPE: dict[tuple[str, str], str] = {
    ("bearing", "base"): "bearing_on_base",
    ("waste", "defective_box"): "waste_to_defective_box",
    ("bearing", "good_box"): "bearing_to_good_box",
}


def get_tag_pick_place_recipe(
    recipe_name: str | None = None,
    *,
    relation: str | None = None,
    source_object: str | None = None,
    destination_object: str | None = None,
    source_tag_id: int | None = None,
    destination_tag_id: int | None = None,
) -> TagPickPlaceRecipe:
    """Return the canonical recipe for object nouns, relation, or explicit name."""
    source_object = resolve_object_name(source_object, default=None)
    destination_object = resolve_object_name(destination_object, default=None)
    if source_object is None and source_tag_id is not None:
        source_object = object_name_from_tag_id(int(source_tag_id))
    if destination_object is None and destination_tag_id is not None:
        destination_object = object_name_from_tag_id(int(destination_tag_id))

    object_pair_recipe = None
    if source_object is not None and destination_object is not None:
        object_pair_recipe = OBJECT_PAIR_TO_RECIPE.get((source_object, destination_object))

    selected = object_pair_recipe or _canonical_recipe_name(recipe_name)
    if selected is None:
        selected = RELATION_TO_RECIPE.get((relation or "").strip().lower())
    selected = selected or DEFAULT_TAG_PICK_PLACE_RECIPE

    try:
        return TAG_PICK_PLACE_RECIPES[selected]
    except KeyError as exc:
        valid = sorted(TAG_PICK_PLACE_RECIPES)
        raise ValueError(f"unknown recipe {selected!r}; valid recipes: {valid}") from exc


def _canonical_recipe_name(recipe_name: str | None) -> str | None:
    if recipe_name is None or str(recipe_name).strip() == "":
        return None
    name = str(recipe_name).strip()
    return RECIPE_ALIASES.get(name, name)


def resolve_object_name(value: Any, *, default: str | None = None) -> str | None:
    if value is None or str(value).strip() == "":
        return default
    raw = str(value).strip()
    canonical = _OBJECT_ALIAS_TO_CANONICAL.get(raw)
    if canonical is not None:
        return canonical
    canonical = _OBJECT_ALIAS_TO_CANONICAL.get(raw.lower())
    if canonical is not None:
        return canonical
    valid = sorted(binding.display_name for binding in OBJECT_TAG_BINDINGS.values())
    raise ValueError(f"unknown object {raw!r}; valid object names: {valid}")


def tag_id_for_object(value: Any) -> int:
    name = resolve_object_name(value)
    if name is None:
        raise ValueError("object name is required")
    return int(OBJECT_TAG_BINDINGS[name].tag_id)


def object_name_from_tag_id(tag_id: int) -> str | None:
    tag_id = int(tag_id)
    for name, binding in OBJECT_TAG_BINDINGS.items():
        if int(binding.tag_id) == tag_id:
            return name
    return None


def object_display_name(value: Any) -> str:
    name = resolve_object_name(value)
    if name is None:
        raise ValueError("object name is required")
    return OBJECT_TAG_BINDINGS[name].display_name


def object_mapping() -> dict[str, dict[str, Any]]:
    return {
        name: binding.to_dict()
        for name, binding in sorted(OBJECT_TAG_BINDINGS.items())
    }


def object_mapping_text() -> str:
    return "\n".join(
        f"- {binding.display_name} ({name}) -> tag {binding.tag_id}"
        for name, binding in sorted(OBJECT_TAG_BINDINGS.items())
    )


def object_name_choices() -> list[str]:
    choices: list[str] = []
    for binding in OBJECT_TAG_BINDINGS.values():
        choices.append(binding.display_name)
        choices.append(binding.canonical_name)
        choices.extend(binding.aliases)
    return sorted(set(choices))


def resolve_pick_place_request(
    *,
    source_object: Any = None,
    destination_object: Any = None,
    source_tag_id: Any = None,
    destination_tag_id: Any = None,
) -> dict[str, Any]:
    source_name = resolve_object_name(source_object, default=None)
    destination_name = resolve_object_name(destination_object, default=None)

    source_id = _optional_int(source_tag_id)
    destination_id = _optional_int(destination_tag_id)

    if source_name is None and source_id is not None:
        source_name = object_name_from_tag_id(source_id)
    if destination_name is None and destination_id is not None:
        destination_name = object_name_from_tag_id(destination_id)

    if source_name is not None:
        expected = tag_id_for_object(source_name)
        if source_id is None:
            source_id = expected
        elif int(source_id) != expected:
            raise ValueError(
                f"source_object {object_display_name(source_name)!r} maps to tag {expected}, "
                f"but source_tag_id={source_id}"
            )

    if destination_name is not None:
        expected = tag_id_for_object(destination_name)
        if destination_id is None:
            destination_id = expected
        elif int(destination_id) != expected:
            raise ValueError(
                f"destination_object {object_display_name(destination_name)!r} maps to tag {expected}, "
                f"but destination_tag_id={destination_id}"
            )

    if source_id is None or destination_id is None:
        raise ValueError(
            "source_object and destination_object are required unless source_tag_id "
            "and destination_tag_id are provided"
        )

    return {
        "source_object": source_name,
        "destination_object": destination_name,
        "source_display_name": None if source_name is None else object_display_name(source_name),
        "destination_display_name": None if destination_name is None else object_display_name(destination_name),
        "source_tag_id": int(source_id),
        "destination_tag_id": int(destination_id),
    }


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def recipe_names() -> list[str]:
    return sorted(TAG_PICK_PLACE_RECIPES)
