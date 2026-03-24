# Release Checklist

## Pre-Release

- Run `python3 -m pytest -q`
- Verify the app starts locally with `python3 app.py`
- Confirm the default example host does not contain personal infrastructure details
- Regenerate icons with `python3 tools/generate_icon_assets.py` if branding changed
- Review `README.md` for feature parity with the current UI

## Build Artifacts

### macOS

```bash
./build_macos.sh
```

Expected output:

- `dist/OpenClawOps.app`

### Windows

```bat
build_windows.bat
```

Expected output:

- `dist\OpenClawOps\`

## Smoke Test

- Open the packaged app
- Add or edit a host profile
- Use `测试连接`
- Run `连接检查`
- Run `环境诊断`
- Confirm warning states show yellow status light and matching status text color
- Confirm logs are written to a user-writable directory

## GitHub Release Draft

- Tag format: `vYYYY.MM.DD` or semantic version
- Title example: `OpenClawOps v2026.03.25`
- Attach:
  - `OpenClawOps.app` packaged as zip for macOS
  - Windows build artifact produced on Windows
- Paste notes from `docs/RELEASE_NOTES_TEMPLATE.md`

## Final Checks

- Push commits and tags
- Publish GitHub release
- Verify repository front page renders README correctly
