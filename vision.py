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
