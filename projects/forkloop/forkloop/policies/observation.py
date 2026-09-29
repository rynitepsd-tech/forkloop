"""Versioned policy input, shared by dataset rendering and inference.

Only observed screenshots, instruction and executed action history belong here.
Image placeholders are adapted to HTTP data URLs or processor PIL inputs by the
caller; their position and all text stay identical.
"""
from __future__ import annotations

from . import action_parse as ap

OBSERVATION_SCHEMA = "forkloop.observation.v3"
#: v3 plus the explicit-memory block (facts the policy itself wrote on earlier steps).
OBSERVATION_SCHEMA_MEMORY = "forkloop.observation.v4-memory"


def resize_for_model(im, *, image_max_side: int, image_scale: float = 1.0):
    """The one image transform shared by serving (StudentPolicy) and training (train_lora): upscale by
    ``image_scale`` (LANCZOS; small text such as authorization codes is resolution-limited: 4/14 exact
    reads at 1x vs 12/14 at 1.5x for Qwen3.8-27B, 2026-09-29), then cap the longest side at
    ``image_max_side``. Returns an RGB PIL image."""
    from PIL import Image

    if im.mode != "RGB":
        im = im.convert("RGB")
    if image_scale and abs(image_scale - 1.0) > 1e-9:
        im = im.resize((max(1, int(round(im.width * image_scale))), max(1, int(round(im.height * image_scale)))),
                       Image.LANCZOS)
    if image_max_side and max(im.size) > image_max_side:
        k = image_max_side / float(max(im.size))
        im = im.resize((max(1, int(round(im.width * k))), max(1, int(round(im.height * k)))), Image.LANCZOS)
    return im


def coordinate_size(space: str, style: str, image: tuple[int, int], screen: tuple[int, int]) -> tuple[int, int]:
    if space == "auto":
        space = "norm1000" if style == "fara" else "image"
    return {"norm1000": (1000, 1000), "norm999": (999, 999), "image": image, "screen": screen}[space]


def scaled_history(history: list[str], screen: tuple[int, int], coords: tuple[int, int]) -> list[str]:
    out = []
    for text in history:
        action, _ = ap.parse_compact(text)
        out.append(ap.to_compact(ap.scale_coords(action, screen, coords)) if action is not None else text)
    return out


def observation_messages(*, instruction: str, history: list[str], step: int | None,
                         screen: tuple[int, int], coords: tuple[int, int], style: str,
                         history_k: int, image_count: int, system_template: str | None = None,
                         instruction_note: str | None = None, nav_macro: bool = False,
                         fara_allowed: tuple[str, ...] | None = None, notes: list[str | None] | None = None,
                         memory: list[str] | None = None) -> list[dict]:
    from .student import build_system_prompt, build_user_text, fara_allowed_actions, format_prompt_override

    if history_k < 0:
        raise ValueError("history_k must be nonnegative")
    if image_count not in (1, 2) or (step == 0 and image_count != 1):
        raise ValueError("expected current only at step zero, otherwise previous/current or current only")
    history = list(history[-history_k:]) if history_k else []
    notes = (list(notes[-history_k:]) if history_k else []) if notes is not None else None
    history = scaled_history(history, screen, coords)
    allowed = fara_allowed if fara_allowed is not None else fara_allowed_actions(nav_macro)
    system = (format_prompt_override(system_template, coords, allowed) if system_template
              else build_system_prompt(style, *coords, fara_allowed=allowed))
    if instruction_note and instruction_note.strip():
        instruction = instruction.rstrip() + "\n\n" + instruction_note.strip()
    content = [{"type": "text", "text": build_user_text(instruction, history, style, step=step, notes=notes,
                                                        memory=memory)}]
    if image_count == 2:
        label = f" ({history[-1]})" if history else ""
        content.extend([{"type": "text", "text": f"Screen BEFORE your last action{label}:"},
                        {"type": "image"}, {"type": "text", "text": "Screen NOW (act on this one):"}])
    content.append({"type": "image"})
    return [{"role": "system", "content": [{"type": "text", "text": system}]},
            {"role": "user", "content": content}]


def http_messages(messages: list[dict], image_urls: list[str], detail: str | None = None) -> list[dict]:
    urls = iter(image_urls)
    out = []
    for message in messages:
        parts = []
        for part in message["content"]:
            if part["type"] == "image":
                img = {"url": next(urls)}
                if detail:
                    img["detail"] = detail
                parts.append({"type": "image_url", "image_url": img})
            else:
                parts.append(dict(part))
        out.append({"role": message["role"], "content": parts[0]["text"] if message["role"] == "system" else parts})
    if next(urls, None) is not None:
        raise ValueError("more images than placeholders")
    return out
