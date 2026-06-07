#!/usr/bin/env python3
"""
workopix - a high-performance local image compressor & converter.

Routes each image to the best available native encoder (pngquant, oxipng,
mozjpeg, cwebp) and falls back to Pillow when a binary isn't installed.
Runs across all CPU cores. Never overwrites originals; never writes a file
that ended up bigger than the source (unless you explicitly convert format).

Quick start (after `pip install workopix`):
    workopix ./photos                       # compress -> ./compressed
    workopix ./photos -r -o ./out           # recurse, custom output dir
    workopix ./photos --convert webp -q 80   # convert everything to webp
    workopix ./photos --convert avif         # best compression, modern format
    workopix ./photos --lossless             # lossless PNG/WebP path
    workopix ./photos --max-width 2000       # downscale large images
    workopix ./photos --dry-run              # show what would happen

Or run without installing:  python workopix.py ./photos
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow is required:  pip install Pillow pillow-avif-plugin")

# Optional AVIF support
try:
    import pillow_avif  # noqa: F401  (registers the AVIF plugin)
    AVIF_OK = True
except ImportError:
    AVIF_OK = False

SUPPORTED_IN = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".bmp", ".tiff", ".tif"}
TARGETS = {"png", "jpeg", "jpg", "webp", "avif"}

# ----- terminal colors -------------------------------------------------------
class C:
    G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; B = "\033[94m"
    DIM = "\033[2m"; BOLD = "\033[1m"; END = "\033[0m"

def _no_color():
    for k in ("G", "Y", "R", "B", "DIM", "BOLD", "END"):
        setattr(C, k, "")


# ----- tool discovery --------------------------------------------------------
def find_tools():
    # Some Homebrew formulae are "keg-only" and not symlinked onto PATH
    # (mozjpeg conflicts with the standard libjpeg), so also look in the
    # known keg locations. An env override wins if set, e.g. CJPEG_BIN.
    extra_dirs = [
        "/opt/homebrew/opt/mozjpeg/bin",   # Apple Silicon keg
        "/usr/local/opt/mozjpeg/bin",      # Intel mac keg
        "/opt/homebrew/bin", "/usr/local/bin",
    ]

    def locate(name):
        env = os.environ.get(f"{name.upper()}_BIN")
        if env and os.access(env, os.X_OK):
            return env
        p = shutil.which(name)
        if p:
            return p
        for d in extra_dirs:
            cand = os.path.join(d, name)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return None

    return {name: locate(name) for name in
            ("pngquant", "oxipng", "zopflipng", "cjpeg", "cwebp")}


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# ----- the per-image worker (must be top-level so it is picklable) -----------
@dataclass
class Task:
    src: str
    dst: str
    target: str          # png | jpeg | webp | avif | "" (keep source format)
    quality: int
    lossless: bool
    max_w: int
    max_h: int
    keep_meta: bool
    tools: dict
    dry_run: bool


@dataclass
class Result:
    src: str
    dst: str
    before: int
    after: int
    status: str          # ok | skip | error | convert
    note: str = ""


def _run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, check=True).stdout


def _load_and_prep(task: Task):
    """Open image, honor EXIF orientation, optionally downscale. Returns a PIL Image."""
    img = Image.open(task.src)
    img = ImageOps.exif_transpose(img)  # bake in rotation so we can drop EXIF
    if task.max_w or task.max_h:
        mw = task.max_w or img.width
        mh = task.max_h or img.height
        if img.width > mw or img.height > mh:
            img.thumbnail((mw, mh), Image.LANCZOS)
    return img


def _encode_png(task, out_path):
    """Lossy via pngquant (+oxipng squeeze), or lossless via oxipng. Falls back to Pillow."""
    t = task.tools
    src = task.src
    # When we may have resized, native tools can't read the in-memory image,
    # so write a temp PNG first.
    resized = bool(task.max_w or task.max_h)
    work_src = src
    tmp_in = None
    if resized:
        img = _load_and_prep(task)
        tmp_in = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        img.save(tmp_in, "PNG")
        work_src = tmp_in
    try:
        if not task.lossless and t.get("pngquant"):
            lo = max(0, task.quality - 20)
            cmd = ["pngquant", f"--quality={lo}-{task.quality}", "--speed", "1",
                   "--force", "--output", out_path]
            if not task.keep_meta:
                cmd.append("--strip")
            cmd.append(work_src)
            try:
                subprocess.run(cmd, stderr=subprocess.DEVNULL, check=True)
            except subprocess.CalledProcessError:
                # pngquant exits 99 if it can't meet the quality floor -> fall back
                shutil.copyfile(work_src, out_path)
            if t.get("oxipng"):
                subprocess.run(["oxipng", "-o", "4", "--strip", "safe", out_path],
                               stderr=subprocess.DEVNULL, check=False)
            return
        if task.lossless and t.get("oxipng"):
            shutil.copyfile(work_src, out_path)
            level = "max"
            subprocess.run(["oxipng", "-o", "4", "--strip",
                            "all" if not task.keep_meta else "none", out_path],
                           stderr=subprocess.DEVNULL, check=False)
            return
        # Pillow fallback
        img = _load_and_prep(task) if not resized else Image.open(work_src)
        if not task.lossless and img.mode in ("RGB", "RGBA"):
            img = img.convert("P", palette=Image.ADAPTIVE, colors=256)
        img.save(out_path, "PNG", optimize=True)
    finally:
        if tmp_in:
            os.unlink(tmp_in)


def _encode_jpeg(task, out_path):
    img = _load_and_prep(task).convert("RGB")
    t = task.tools
    if t.get("cjpeg"):
        ppm = io.BytesIO()
        img.save(ppm, "PPM")
        data = _run(["cjpeg", "-quality", str(task.quality), "-optimize",
                     "-progressive"], stdin=ppm.getvalue())
        Path(out_path).write_bytes(data)
    else:
        img.save(out_path, "JPEG", quality=task.quality,
                 optimize=True, progressive=True)


def _encode_webp(task, out_path):
    img = _load_and_prep(task)
    if task.lossless:
        img.save(out_path, "WEBP", lossless=True, method=6,
                 exif=b"" if not task.keep_meta else img.info.get("exif", b""))
    else:
        img.save(out_path, "WEBP", quality=task.quality, method=6)


def _encode_avif(task, out_path):
    if not AVIF_OK:
        raise RuntimeError("AVIF support missing: pip install pillow-avif-plugin")
    img = _load_and_prep(task)
    if task.lossless:
        img.save(out_path, "AVIF", quality=100, speed=4)
    else:
        img.save(out_path, "AVIF", quality=task.quality, speed=4)


ENCODERS = {"png": _encode_png, "jpeg": _encode_jpeg, "jpg": _encode_jpeg,
            "webp": _encode_webp, "avif": _encode_avif}


def process(task: Task) -> Result:
    src = task.src
    before = os.path.getsize(src)
    try:
        ext = Path(src).suffix.lower().lstrip(".")
        converting = bool(task.target)
        fmt = task.target or ("jpeg" if ext in ("jpg", "jpeg") else ext)
        if fmt not in ENCODERS:
            return Result(src, "", before, before, "skip", f"unsupported: .{ext}")

        if task.dry_run:
            return Result(src, task.dst, before, before, "skip", "dry-run")

        Path(task.dst).parent.mkdir(parents=True, exist_ok=True)
        tmp_out = task.dst + ".tmp"
        ENCODERS[fmt](task, tmp_out)
        after = os.path.getsize(tmp_out)

        # Keep-smaller rule (only when format is unchanged)
        if not converting and after >= before:
            os.unlink(tmp_out)
            shutil.copyfile(src, task.dst)
            return Result(src, task.dst, before, before, "skip", "already optimal")

        os.replace(tmp_out, task.dst)
        status = "convert" if converting else "ok"
        return Result(src, task.dst, before, after, status)
    except Exception as e:  # noqa: BLE001 - one bad file shouldn't kill the batch
        return Result(src, "", before, before, "error", str(e)[:80])


# ----- orchestration ---------------------------------------------------------
def gather(root: Path, recursive: bool, out_dir: Path):
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        if not p.is_file():
            continue
        if p.suffix.lower() not in SUPPORTED_IN:
            continue
        # don't reprocess our own outputs
        try:
            if out_dir in p.resolve().parents or p.resolve().parent == out_dir.resolve():
                continue
        except (OSError, ValueError):
            pass
        yield p


def main():
    ap = argparse.ArgumentParser(
        description="High-performance local image compressor & converter.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("path", help="image file or folder")
    ap.add_argument("-o", "--output-dir", default="compressed",
                    help="output folder (default: ./compressed)")
    ap.add_argument("-r", "--recursive", action="store_true", help="recurse into subfolders")
    ap.add_argument("-q", "--quality", type=int, default=80,
                    help="lossy quality 0-100 (default: 80)")
    ap.add_argument("--convert", choices=sorted(TARGETS), metavar="FORMAT",
                    help="convert every image to: png, jpeg, webp, avif")
    ap.add_argument("--lossless", action="store_true", help="lossless PNG/WebP/AVIF path")
    ap.add_argument("--max-width", type=int, default=0, help="downscale if wider than this")
    ap.add_argument("--max-height", type=int, default=0, help="downscale if taller than this")
    ap.add_argument("--keep-metadata", action="store_true", help="preserve EXIF/metadata")
    ap.add_argument("--workers", type=int, default=os.cpu_count(), help="parallel workers")
    ap.add_argument("--dry-run", action="store_true", help="show plan, write nothing")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    if args.no_color or not sys.stdout.isatty():
        _no_color()

    target = "jpeg" if args.convert == "jpg" else (args.convert or "")
    if target == "avif" and not AVIF_OK:
        sys.exit(f"{C.R}AVIF requested but plugin missing.{C.END} "
                 f"Install:  pip install pillow-avif-plugin")

    src_path = Path(args.path)
    out_dir = Path(args.output_dir)
    tools = find_tools()

    # Build task list
    if src_path.is_file():
        base = src_path.parent
        files = [src_path]
    elif src_path.is_dir():
        base = src_path
        files = list(gather(src_path, args.recursive, out_dir))
    else:
        sys.exit(f"{C.R}Not found:{C.END} {src_path}")

    if not files:
        sys.exit(f"{C.Y}No supported images found.{C.END}")

    def dst_for(p: Path) -> str:
        rel = p.relative_to(base) if src_path.is_dir() else p.name
        rel = Path(rel)
        if target:
            ext = ".jpg" if target == "jpeg" else f".{target}"
            rel = rel.with_suffix(ext)
        return str(out_dir / rel)

    tasks = [Task(str(p), dst_for(p), target, args.quality, args.lossless,
                  args.max_width, args.max_height, args.keep_metadata, tools,
                  args.dry_run) for p in files]

    # Banner
    avail = [k for k, v in tools.items() if v]
    missing = [k for k, v in tools.items() if not v]
    print(f"{C.BOLD}workopix{C.END}  {len(tasks)} image(s)  ·  {args.workers} workers")
    print(f"  encoders: {C.G}{', '.join(avail) or 'none (Pillow only)'}{C.END}"
          + (f"   {C.DIM}missing: {', '.join(missing)}{C.END}" if missing else ""))
    print(f"  mode: {'convert→' + target if target else 'optimize in place'}"
          f"{' · lossless' if args.lossless else f' · q{args.quality}'}")
    if args.dry_run:
        print(f"  {C.Y}DRY RUN — nothing will be written{C.END}")
    print()

    t0 = time.time()
    total_before = total_after = 0
    ok = conv = skip = err = 0
    done = 0
    n = len(tasks)

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, t): t for t in tasks}
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            total_before += r.before
            total_after += r.after
            name = Path(r.src).name
            pct = (1 - r.after / r.before) * 100 if r.before else 0
            tag = f"{C.DIM}[{done}/{n}]{C.END}"
            if r.status in ("ok", "convert"):
                if r.status == "ok":
                    ok += 1
                else:
                    conv += 1
                arrow = f"→{target}" if r.status == "convert" else ""
                print(f"{tag} {C.G}✓{C.END} {name} {arrow}  "
                      f"{human(r.before)} → {human(r.after)}  "
                      f"{C.G}-{pct:.0f}%{C.END}")
            elif r.status == "skip":
                skip += 1
                print(f"{tag} {C.Y}•{C.END} {name}  {C.DIM}{r.note}{C.END}")
            else:
                err += 1
                print(f"{tag} {C.R}✗{C.END} {name}  {C.R}{r.note}{C.END}")

    dt = time.time() - t0
    saved = total_before - total_after
    pct = (saved / total_before * 100) if total_before else 0
    print()
    print(f"{C.BOLD}Done{C.END} in {dt:.1f}s  ·  "
          f"{C.G}{ok} optimized{C.END}, {conv} converted, {skip} skipped, "
          f"{C.R if err else C.DIM}{err} errors{C.END}")
    if not args.dry_run:
        print(f"  {human(total_before)} → {human(total_after)}  "
              f"({C.G}saved {human(saved)}, -{pct:.1f}%{C.END})")
        print(f"  output: {C.B}{out_dir.resolve()}{C.END}")


if __name__ == "__main__":
    main()
