# Release Notes Template

## Highlights

- Desktop operations workflow streamlined to `连接检查 -> 环境诊断 -> 修复并升级 -> 验证 OpenClaw -> 源码构建兜底`
- Built-in multi-host profile management from the UI
- Background SSH connection testing inside the host editor
- Status light and status text color kept in sync
- Version drift detection integrated into `环境诊断`

## Packaging

- Shared app icon design for macOS and Windows
- macOS app bundle built with PyInstaller
- Windows packaging script included for building on Windows

## Fixes

- Packaged app now writes logs and config to user-writable directories
- Version parsing accepts banner-style outputs such as `OpenClaw 2026.3.23-2 (abcdef0)`

## Notes

- Windows executable must be built on Windows
