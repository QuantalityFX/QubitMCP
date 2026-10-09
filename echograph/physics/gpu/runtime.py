"""Setup-time check for the binary Warp runtime and usable CUDA devices."""
import importlib
import sys


def check_runtime():
    try:
        wp = importlib.import_module("warp")
    except ImportError as exc:
        raise RuntimeError("Warp is not installed in this Python environment. Run setup.bat core.") from exc
    wp.init()
    print("[setup] Warp " + str(wp.__version__) + " · Python: " + sys.executable)
    devices = list(wp.get_cuda_devices())
    device = devices[0] if devices else wp.get_device("cpu")
    # Allocating and transferring actual data detects missing native libraries
    # and unusable drivers rather than merely finding the Python module.
    probe = wp.array([1.0, 2.0, 3.0], dtype=wp.float32, device=device)
    values = probe.numpy()
    if values.tolist() != [1.0, 2.0, 3.0]:
        raise RuntimeError("Warp device transfer validation failed.")
    for available in devices:
        print("[setup] CUDA available: " + str(available.name) + " (" + str(available.alias) + ")")
    if not devices:
        print("[setup] Warp CPU runtime is ready. CUDA is unavailable; cloth will use CPU XPBD.")
        print("[setup] CUDA requires a supported NVIDIA GPU and compatible NVIDIA driver.")
    return bool(devices)


def main():
    try:
        check_runtime()
    except Exception as exc:
        print("[setup] ERROR: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
