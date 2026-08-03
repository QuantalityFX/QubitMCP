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
choose a mode, and click Generate. The node stores generated files under:

```text
logs\image_gs_splat
```

`Exact Pixel` is the default mode. It stages the source as RGBA PNG and writes a
front-facing deterministic PLY with one Gaussian splat per visible pixel, capped
at 5 million splats by default. This is the preferred path when the goal is a
filled single-image splat sheet with predictable coverage. Transparent pixels
are skipped. The PLY records are interleaved from coarse full-frame coverage to
fine detail so partial viewport samples still represent the whole image.
Viewport display uses a sampled preview with compensated splat radius for large
exact exports, while the generated PLY on disk remains full resolution.

`Image-GS` mode keeps the previous optimizer workflow: the node stages the image
into the Image-GS checkout, runs `main.py`, converts the latest checkpoint to a
QubitMCP-compatible PLY splat, and writes the same manifest/debug outputs.

In `Image-GS` mode, `Auto HQ` reads the staged image dimensions and image detail,
then chooses high-quality values for splat count, optimization steps, render
height, radius scale, alpha, and the PLY export cap. It also keeps Image-GS
progressive optimization enabled so the runtime starts with a smaller seed set
and adds Gaussians into high-error regions during training. Turn `Auto HQ` off
when you want to tune those fields manually.

After a successful run, the node writes a QC report into the manifest and debug
report. QC compares the rendered Image-GS reconstruction back to the staged
source image when a render image is available, and also records splat density,
export cap status, PSNR-style similarity, and edge/detail retention estimates.

During Image-GS checkpoint-to-PLY conversion, Auto HQ also applies an export scale and
coverage guard. The guard clamps oversized pixel axes, limits extreme
anisotropy, drops broken scale outliers, applies a small coverage boost, and
adds enough Z thickness for stable viewport projection. This keeps bad
Gaussians from becoming long splats while reducing holes in the exported PLY.

Use View on the node or connect it to a `scene` node to load the generated PLY
in the viewport. If the node reports missing Image-GS imports, rerun
`setup.bat` or `nodes\image_gs\setup_image_gs.bat`.
