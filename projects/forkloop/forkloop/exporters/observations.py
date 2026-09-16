"""Screenshot provenance for a recorded observation, never controller answers."""
from pathlib import Path

from PIL import Image

from ..policies.observation import OBSERVATION_SCHEMA


def image_fields(episode_dir: Path, steps: list[dict], index: int) -> dict:
    current = steps[index]
    step = int(current["i"])
    if step != index or any(int(s["i"]) != i for i, s in enumerate(steps[:index])):
        raise ValueError("observation export requires a contiguous episode starting at step zero")
    selected = [steps[index - 1], current] if index else [current]
    paths = []
    sizes = []
    for s in selected:
        if not s.get("shot_before"):
            raise ValueError(f"missing screenshot for step {s['i']}")
        path = (episode_dir / s["shot_before"]).resolve()
        if not path.is_relative_to(episode_dir.resolve()):
            raise ValueError("screenshot crosses episode boundary")
        with Image.open(path) as im:
            im.verify()
            sizes.append(im.size)
        paths.append(str(path))
    if len(set(sizes)) != 1:
        raise ValueError("screen size changed within observation pair")
    return {"schema_version": OBSERVATION_SCHEMA, "images": paths,
            "image_roles": ["previous", "current"] if index else ["current"],
            "image_steps": [step - 1, step] if index else [step],
            "screen_size": list(sizes[-1]), "history_coordinate_space": "screen"}
