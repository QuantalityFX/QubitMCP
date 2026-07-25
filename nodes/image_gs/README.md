# Image-GS Runtime

QubitMCP does not vendor the Image-GS repository. The main `setup.bat` calls
`setup_image_gs.bat`, which downloads `https://github.com/NYU-ICL/image-gs.git`
into the writable app home:

```text
%QUBITMCP_HOME%\third_party\image-gs
```

The checkout has its own `.venv` so Image-GS dependencies do not pollute the
main QubitMCP environment. Setup installs PyTorch, Image-GS Python dependencies,
fused-ssim, and the bundled `gsplat` package. Set `QUBITMCP_SKIP_IMAGE_GS=1`
before running setup to skip this optional runtime.

## Node usage

Create an `image_gs_splat` node, paste an HTTP image URL or local image path,
and click Generate. The node stages the image into the Image-GS checkout, runs
`main.py`, converts the latest checkpoint to a QubitMCP-compatible PLY splat,
and stores generated files under:

```text
logs\image_gs_splat
```

Use View on the node or connect it to a `scene` node to load the generated PLY
in the viewport. If the node reports missing Image-GS imports, rerun
`setup.bat` or `nodes\image_gs\setup_image_gs.bat`.
