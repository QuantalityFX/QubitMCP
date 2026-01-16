# tools/print_ply_header.py
from pathlib import Path
import sys

def print_ply_header(path: Path):
    with path.open("rb") as f:
        while True:
            line = f.readline()
            if not line:
                break
            s = line.decode("utf-8", errors="replace").rstrip()
            print(s)
            if s == "end_header":
                break

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python echograph/util/print_ply_header.py E:/GaussingSplats/models/input.ply")
        raise SystemExit(2)
    print_ply_header(Path(sys.argv[1]))
