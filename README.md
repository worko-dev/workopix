# workopix

High-performance local image compressor & converter for your own machine — like the
output quality of TinyPNG, but offline, free, and with no upload limits.

`workopix` walks a folder, routes each image to the **best native encoder available**
(`pngquant`, `oxipng`, `mozjpeg`, `cwebp`), and falls back to Pillow when a binary isn't
installed. It runs across all your CPU cores and **never touches your originals**.

## Why

Pure-Python compressors can't match TinyPNG because the quality comes from native engines
like `libimagequant` (the core of `pngquant`) and `mozjpeg`. `workopix` orchestrates those
engines and handles the batching, parallelism, format conversion, and safety rules around them.

## Install

```bash
pip install workopix            # core (PNG, JPEG, WebP)
pip install "workopix[avif]"    # add AVIF support
```

That alone works using Pillow. To unlock the best output, install the native encoders —
`workopix` auto-detects whatever is present:

```bash
# macOS (Homebrew)
brew install pngquant oxipng mozjpeg webp

# Ubuntu / Debian
sudo apt install pngquant oxipng webp libjpeg-turbo-progs

# Windows (scoop)
scoop install pngquant oxipng libwebp
```

> **macOS note:** `mozjpeg` is keg-only and isn't symlinked onto your `PATH` (it conflicts
> with the system `libjpeg`). You don't need to edit your `PATH` — `workopix` looks in the
> Homebrew keg location automatically. You can ignore the `LDFLAGS`/`CPPFLAGS` advice Homebrew
> prints; that's only for compiling C code.

## Usage

```bash
workopix ./photos                    # compress -> ./compressed
workopix ./photos -r -o ./out        # recurse into subfolders, custom output
workopix ./photos --convert webp -q 80   # convert everything to WebP
workopix ./photos --convert avif         # smallest files, modern format
workopix ./photos --lossless             # lossless PNG / WebP path
workopix ./photos --max-width 2000       # downscale large images first
workopix ./photos --dry-run              # preview, write nothing
```

## Options

| Flag | Description |
|------|-------------|
| `path` | Image file or folder to process |
| `-o, --output-dir` | Output folder (default: `./compressed`) |
| `-r, --recursive` | Recurse into subfolders |
| `-q, --quality` | Lossy quality 0–100 (default: 80) |
| `--convert FORMAT` | Convert every image to `png`, `jpeg`, `webp`, or `avif` |
| `--lossless` | Lossless PNG / WebP / AVIF path |
| `--max-width N` | Downscale if wider than N px |
| `--max-height N` | Downscale if taller than N px |
| `--keep-metadata` | Preserve EXIF/metadata (stripped by default) |
| `--workers N` | Parallel workers (default: all CPU cores) |
| `--dry-run` | Show the plan without writing files |
| `--no-color` | Disable colored output |

## How it works

| Format | Lossy | Lossless | Fallback |
|--------|-------|----------|----------|
| PNG | `pngquant` + `oxipng` squeeze | `oxipng` | Pillow adaptive palette |
| JPEG | `mozjpeg` (`cjpeg`) | — | Pillow optimize + progressive |
| WebP | Pillow/`cwebp` (`method 6`) | Pillow lossless | — |
| AVIF | Pillow + `pillow-avif-plugin` | Pillow | — |

## Safety guarantees

- **Originals are never modified.** Output goes to a separate folder mirroring your structure.
- **Files never grow.** When optimizing in the same format, `workopix` keeps whichever is
  smaller, so a file can't accidentally end up larger.
- **No surprise rotations.** EXIF orientation is baked in before metadata is stripped.
- **One bad file won't kill the batch** — errors are reported per-file and the run continues.

## Notes

- AVIF encoding is slower than the other formats (that's the codec, not the tool).
- `pngquant` will refuse a file if it can't hit the quality floor without too much loss; when
  that happens `workopix` falls back to copying rather than producing something ugly. Lower
  `-q` to be more aggressive.

## License

MIT © Zubair Mahboob
