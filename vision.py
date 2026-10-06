"""Turn an image file into a data URI for vision-capable providers."""
import base64
import io

MAX_SIDE = 1024
MAX_BYTES = 4_000_000


def image_data_uri(path: str):
    """Return a data URI (JPEG/PNG, downscaled), or None if it cannot be made."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            has_alpha = img.mode in ("RGBA", "LA", "P")
            buf = io.BytesIO()
            if has_alpha:
                img.convert("RGBA").save(buf, "PNG")
                mime = "image/png"
            else:
                img.convert("RGB").save(buf, "JPEG", quality=85)
                mime = "image/jpeg"
        data = buf.getvalue()
        if len(data) > MAX_BYTES:
            return None
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def strip_old_images(messages: list) -> None:
    """Keep only the newest image in history so context does not balloon."""
    marked = [i for i, m in enumerate(messages)
              if isinstance(m.get("content"), list)
              and any(p.get("type") == "image_url" for p in m["content"])]
    for i in marked[:-1]:
        text = " ".join(p.get("text", "") for p in messages[i]["content"] if p.get("type") == "text")
        messages[i] = {"role": messages[i]["role"], "content": (text + " [earlier image removed]").strip()}


DESCRIBE_PROMPT = (
    "Describe this image for another AI that cannot see it. Transcribe ALL visible text exactly "
    "(keep the language), then describe layout, UI elements, charts, numbers, people/objects and "
    "colors. Be factual. If something is unreadable, say so. Do not guess.")


def describe_image(path: str, question: str = "", providers=None, client_factory=None, timeout: float = 120.0):
    """Ask the best vision-capable provider to describe the image, so a bigger text-only model can use it.

    Returns (text, "model (preset)") or (None, reason). Never raises, never echoes keys.
    """
    import router
    from providers import active_providers
    provs = router.rank_providers(providers if providers is not None else active_providers(), need_vision=True)
    provs = [p for p in provs if p.get("vision")]
    if not provs:
        return None, "no vision-capable provider is enabled"
    uri = image_data_uri(path)
    if not uri:
        return None, "image could not be loaded or is too large"
    prompt = DESCRIBE_PROMPT + (f"\nThe user's question about it: {question}" if question else "")
    last = "unknown error"
    for p in provs:
        try:
            if client_factory:
                client = client_factory(p)
            else:
                from api_retry import make_client
                client = make_client(p["base_url"], p["api_key"])
            resp = client.chat.completions.create(
                model=p["model"], max_tokens=1200, temperature=0, timeout=timeout,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": uri}}]}])
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text, router.label(p)
            last = "empty answer"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e).replace(p.get('api_key', ''), '***')[:120]}"
    return None, last
