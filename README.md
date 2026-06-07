# workopix

High-performance local image compressor & converter — TinyPNG-quality output, offline and
free. `workopix` walks a folder, routes each image to the best native encoder available
(`pngquant`, `oxipng`, `mozjpeg`, `cwebp`), falls back to Pillow, runs across all CPU cores,
and **never touches your originals**.

## Install

```bash
git clone https://github.com/worko-dev/workopix.git
cd workopix
pip install --user ".[avif]"     # drop [avif] if you don't need AVIF
```

If your shell then says `workopix: command not found`, the user scripts dir isn't on your
`PATH`. Add this once to `~/.zshrc` (or `~/.bashrc`):

```bash
export PATH="$(python3 -m site --user-base)/bin:$PATH"
```

For the best output quality, install the native encoders (auto-detected if present):

```bash
# macOS (Homebrew)
brew install pngquant oxipng mozjpeg webp
# Ubuntu / Debian
sudo apt install pngquant oxipng webp libjpeg-turbo-progs
```

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
| `--max-width N` / `--max-height N` | Downscale if larger than N px |
| `--keep-metadata` | Preserve EXIF/metadata (stripped by default) |
| `--workers N` | Parallel workers (default: all CPU cores) |
| `--dry-run` | Show the plan without writing files |

## Notes

- Originals are never modified; output goes to a separate folder. When optimizing in the same
  format, `workopix` keeps whichever file is smaller, so files never grow.
- AVIF encoding is slower than other formats (that's the codec, not the tool).

## License

MIT © Worko Dev
