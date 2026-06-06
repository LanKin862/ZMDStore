from __future__ import annotations

import ctypes

# 启用 Windows DPI 感知，必须在程序最早期执行，防止被打包工具或后续库（如 PIL/cv2）锁定状态
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import argparse
import base64
import json
import mimetypes
import sys
import time
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True, write_through=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True, write_through=True)

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _abs(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = BASE_DIR / p
    return p.resolve()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _data_url(path: Path, size: int | None = 128) -> str:
    with Image.open(path) as img:
        if size:
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _image_meta(path: Path) -> dict:
    width = height = 0
    try:
        with Image.open(path) as img:
            width, height = img.size
    except Exception:
        pass
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return {
        "name": path.stem,
        "fileName": path.name,
        "path": str(path),
        "thumb": _data_url(path),
        "mime": mime,
        "width": width,
        "height": height,
    }


def list_resources() -> dict:
    item_dir = BASE_DIR / "item"
    region_dir = BASE_DIR / "region"
    items = []
    regions = []
    if item_dir.exists():
        items = [_image_meta(p) for p in sorted(item_dir.iterdir()) if p.suffix.lower() in IMAGE_SUFFIXES]
    if region_dir.exists():
        for p in sorted(region_dir.iterdir()):
            if p.suffix.lower() in IMAGE_SUFFIXES and not p.stem.startswith("already_in_"):
                regions.append(_image_meta(p))
    return {"items": items, "regions": regions, "baseDir": str(BASE_DIR)}


def compose_liquid(liquid: str | None, container: str) -> str:
    temp_dir = BASE_DIR / "temp" / "item"
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_path = temp_dir / f"liquid_output_{int(time.time() * 1000)}.png"
    img_b = Image.open(_abs(container)).convert("RGBA")
    if liquid:
        img_a = Image.open(_abs(liquid)).convert("RGBA")
        scale = 40 / 88
        img_a = img_a.resize((max(1, int(img_a.width * scale)), max(1, int(img_a.height * scale))), Image.Resampling.LANCZOS)
        img_b.paste(img_a, ((img_b.width - img_a.width) // 2, (img_b.height - img_a.height) // 2), img_a)
    img_b.save(output_path, format="PNG")
    return str(output_path)


def save_image(payload: dict) -> dict:
    data_url = payload["dataUrl"]
    target_type = payload["type"]
    name = payload["name"].strip()
    fmt = payload["format"].lower()
    if target_type not in {"item", "region"}:
        raise ValueError("资源类型必须是 item 或 region")
    if not name:
        raise ValueError("文件名不能为空")
    if fmt not in {"png", "jpg", "jpeg", "webp"}:
        raise ValueError("不支持的图片格式")

    _, encoded = data_url.split(",", 1)
    raw = base64.b64decode(encoded)
    img = Image.open(BytesIO(raw)).convert("RGBA")
    target_dir = BASE_DIR / target_type
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = "jpg" if fmt == "jpeg" else fmt
    output_path = target_dir / f"{name}.{suffix}"
    temp_path = target_dir / f"_{name}.{suffix}"

    save_format = "JPEG" if suffix == "jpg" else suffix.upper()
    if suffix == "jpg":
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.getchannel("A"))
        bg.save(temp_path, format=save_format, quality=95)
    else:
        img.save(temp_path, format=save_format)
    if output_path.exists():
        output_path.unlink()
    temp_path.rename(output_path)
    return _image_meta(output_path)


def delete_image(path: str) -> None:
    p = _abs(path)
    if not (_is_inside(p, BASE_DIR / "item") or _is_inside(p, BASE_DIR / "region")):
        raise ValueError("只能删除 item 或 region 目录内的图片")
    p.unlink(missing_ok=True)


def run_transport(config: dict) -> None:
    import auto_click

    auto_click.reset_stop()
    auto_click.run_transport(
        config["begin"],
        config["end"],
        config["item"],
        int(config["times"]),
        config.get("resolution", "自动检测"),
        liquid_mode=bool(config.get("liquidMode", False)),
    )


def run_batch(payload: dict) -> None:
    import auto_click

    auto_click.reset_stop()
    tasks = payload["tasks"]
    resolution = payload.get("resolution", "自动检测")
    for index, task in enumerate(tasks):
        if auto_click.should_stop():
            break
        print(f"\n{'=' * 20}\n[任务 {index + 1}/{len(tasks)}] 正在执行: {task['beginName']} -> {task['endName']}\n{'=' * 20}", flush=True)
        auto_click.run_transport(
            task["begin"],
            task["end"],
            task["item"],
            int(task["times"]),
            resolution,
            liquid_mode=bool(task.get("liquidMode", False)),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["resources", "image-data", "compose-liquid", "save-image", "delete-image", "run", "batch", "read-document"])
    parser.add_argument("--json", default="{}")
    args = parser.parse_args()
    payload = json.loads(args.json)

    try:
        if args.command == "resources":
            print(json.dumps(list_resources(), ensure_ascii=False))
        elif args.command == "image-data":
            print(_data_url(_abs(payload["path"]), None))
        elif args.command == "compose-liquid":
            print(compose_liquid(payload.get("liquid"), payload["container"]))
        elif args.command == "save-image":
            print(json.dumps(save_image(payload), ensure_ascii=False))
        elif args.command == "delete-image":
            delete_image(payload["path"])
            print("OK")
        elif args.command == "run":
            run_transport(payload)
        elif args.command == "batch":
            run_batch(payload)
        elif args.command == "read-document":
            doc_path = BASE_DIR / "Document.md"
            if doc_path.exists():
                print(doc_path.read_text(encoding="utf-8"))
            else:
                print("Document.md not found.")
        return 0
    except KeyboardInterrupt:
        print("任务已停止", flush=True)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
