# MaterialGenerator Plugin (Unreal Editor)

This plugin creates a Matrix Rain master material (and optional material instance) from a toolbar button.

## Folder to copy into your Unreal project

Copy this folder into your Unreal project:

`Plugins/MaterialGenerator`

The plugin root should contain `MaterialGenerator.uplugin`.

## Build/Enable

1. Close Unreal Editor.
2. Copy `MaterialGenerator` into `<YourUnrealProject>/Plugins/`.
3. Right-click `<YourProject>.uproject` and run **Generate Visual Studio project files**.
4. Open the project solution and build `Development Editor` (or open the project and let Unreal build modules).
5. Launch the editor and enable the plugin if prompted.

## Usage

1. In the Level Editor top toolbar (near the Play controls), click **Material Generator**.
2. Fill in:
   - Package Path (type it or use `Browse...` to pick a folder under `Content`; supports `Qubit/Materials`, `/Game/Qubit/Materials`, or disk paths under `.../Content/...`)
   - Master Material Name
   - Optional Glyph Texture using the picker (browse or drag/drop from Content Browser)
   - Optional Material Instance name
3. Click **Generate Material**.

The plugin generates:

- A master material with a `Custom` HLSL node wired for Matrix Rain.
- All material parameter inputs and graph wiring.
- Optional default material instance.

## Notes

- Plugin type is **Editor**; it is intended for authoring assets in editor.
- Runtime parameter animation should be done with MID or MPC from your game code/blueprints.
