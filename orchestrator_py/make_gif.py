"""Stitch the per-generation frames of a run into an animated GIF.

    python orchestrator_py/make_gif.py              # latest run in orchestrator_py/runs
    python orchestrator_py/make_gif.py <run_dir>    # a specific run
"""
import argparse
import glob
import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Pillow library is required. Please install it using: pip install Pillow")
    sys.exit(1)

from runs import latest_run_dir


def create_gif(run_dir, width=900, duration=600):
    # Frame names are zero-padded (generation_007.png), so a plain sort is generation order
    files = sorted(glob.glob(os.path.join(run_dir, "frames", "generation_*.png")))
    if not files:
        print(f"No generation frames found in {os.path.join(run_dir, 'frames')}")
        return None

    print(f"Found {len(files)} frames. Stitching into GIF...")
    images = []
    for f in files:
        with Image.open(f) as im:
            # Full-size frames are 2000x2400; downscale so the GIF stays a few MB at most
            h = round(im.height * width / im.width)
            images.append(im.convert("RGB").resize((width, h), Image.LANCZOS))

    gif_path = os.path.join(run_dir, "evolution.gif")
    images[0].save(
        gif_path,
        save_all=True,
        append_images=images[1:],
        duration=duration,  # milliseconds per frame
        loop=0              # 0 means loop infinitely
    )
    print(f"Success! GIF saved to: {gif_path}")
    return gif_path


def main():
    parser = argparse.ArgumentParser(description="Make an evolution GIF from a DeCFD run folder")
    parser.add_argument("run_dir", nargs="?", help="Run folder (default: the latest run)")
    parser.add_argument("--width", type=int, default=900, help="GIF width in pixels")
    parser.add_argument("--duration", type=int, default=600, help="Milliseconds per frame")
    args = parser.parse_args()

    run_dir = args.run_dir or latest_run_dir()
    if not run_dir or not os.path.isdir(run_dir):
        print("No run folder found. Run app.py first, or pass a run folder explicitly.")
        sys.exit(1)
    create_gif(run_dir, args.width, args.duration)


if __name__ == "__main__":
    main()
