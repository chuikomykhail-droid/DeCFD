"""Download the published surface of one SuperWing shape: the cell-centre coordinates of the
first stored condition of that shape, from data_surf/data_surf.npy.zst.

    python pilot/superwing/fetch_surface.py <row> <out.npy>

`row` is the shape's first row in data_surf (index.npy lists shape and condition per row; with
all eight conditions stored for every earlier shape, shape n starts at row 8n). The file is
zstd-compressed, so it is streamed and decompressed up to that row: about 3 MB per row read.
Needs numpy and zstandard. The data is CC BY-SA 4.0 (yunplus/SuperWing).
"""
import sys, urllib.request
import numpy as np, zstandard

URL = "https://huggingface.co/datasets/yunplus/SuperWing/resolve/main/data_surf/data_surf.npy.zst"
row, out = int(sys.argv[1]), sys.argv[2]
N, C = 44096, 9
with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "decfd-pilot"})) as resp:
    reader = zstandard.ZstdDecompressor().stream_reader(resp)
    head = reader.read(128)
    hlen = head.index(b"\n") + 1
    assert hlen == 128, head
    start = row * C * N * 8
    skipped = 0
    while skipped < start:
        chunk = reader.read(min(1 << 20, start - skipped))
        if not chunk:
            sys.exit("stream ended early")
        skipped += len(chunk)
    need = 3 * N * 8
    buf = b""
    while len(buf) < need:
        buf += reader.read(need - len(buf))
xyz = np.frombuffer(buf, dtype="<f8").reshape(3, N).T.copy()
np.save(out, xyz)
print(out, xyz.shape, xyz.min(0), xyz.max(0))
