"""Gemini wrapper: structured text generation + image generation (Nano Banana).

The only module that touches the google-genai SDK. Model names come from
settings so they can be swapped without code changes.
"""
import json
from pathlib import Path

from app import db
from app.config import GEMINI_API_KEY

_client = None


class GeminiError(RuntimeError):
    pass


def client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise GeminiError('GEMINI_API_KEY is not set in ~/.adops/.env')
        from google import genai
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def gen_text(prompt: str, schema: dict = None, system: str = None, model: str = None) -> str | dict:
    """Generate text; with `schema` (JSON Schema) returns a parsed object."""
    from google.genai import types
    model = model or db.setting('gemini_text_model', 'gemini-2.5-flash')
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type='application/json' if schema else None,
        response_schema=schema,
        temperature=0.8 if not schema else 0.7,
    )
    resp = client().models.generate_content(model=model, contents=prompt, config=config)
    text = resp.text
    if schema:
        try:
            return json.loads(text)
        except (ValueError, TypeError) as exc:
            raise GeminiError(f'Gemini returned unparseable JSON: {exc}: {text[:400]}')
    return text


def analyze_image(image_path: str, prompt: str, schema: dict = None) -> dict | str:
    """Vision call on a local image (layout analysis for the cloner)."""
    from google.genai import types
    model = db.setting('gemini_vision_model', 'gemini-2.5-flash')
    data = Path(image_path).read_bytes()
    mime = 'image/png' if image_path.lower().endswith('.png') else 'image/jpeg'
    part = types.Part.from_bytes(data=data, mime_type=mime)
    config = types.GenerateContentConfig(
        response_mime_type='application/json' if schema else None,
        response_schema=schema,
    )
    resp = client().models.generate_content(model=model, contents=[part, prompt], config=config)
    if schema:
        try:
            return json.loads(resp.text)
        except (ValueError, TypeError) as exc:
            raise GeminiError(f'Gemini vision returned unparseable JSON: {exc}: {resp.text[:400]}')
    return resp.text


def gen_image(prompt: str, ref_image_paths: list[str] = (), out_path: str = None) -> str:
    """Generate an image, optionally conditioned on reference images. Returns the PNG path."""
    from google.genai import types
    model = db.setting('gemini_image_model', 'gemini-2.5-flash-image')
    contents = []
    for path in ref_image_paths or []:
        p = Path(path)
        if not p.exists():
            continue
        suffix = p.suffix.lower()
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp', '.svg': 'image/svg+xml'}.get(suffix)
        if not mime or mime == 'image/svg+xml':  # image models don't take SVG
            continue
        contents.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))
    contents.append(prompt)
    resp = client().models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(response_modalities=['IMAGE', 'TEXT']))
    for cand in resp.candidates or []:
        for part in cand.content.parts or []:
            inline = getattr(part, 'inline_data', None)
            if inline and inline.data:
                Path(out_path).write_bytes(inline.data)
                return out_path
    raise GeminiError('Gemini returned no image data (prompt may have been refused): '
                      + (resp.text or '')[:300])
