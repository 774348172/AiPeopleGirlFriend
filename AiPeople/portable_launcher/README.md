# AiPeople portable launcher

This directory contains the Windows launcher source. It builds with the C# compiler included in .NET Framework 4.8 and does not require the .NET SDK.

Build and run the non-model smoke tests:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\portable_launcher\build.ps1
```

The default output is `portable_launcher/bin/AiPeople.exe`. Place that executable at the root of a complete portable package:

```text
AiPeople.exe
runtime/python.exe
app/tools/baiweixi_chat_app.py
assets/gemma4_nf4_runtime_manifest.json
data/
logs/
```

Supported launcher options:

- `--no-browser`: start the service and perform the full health identity check without opening the browser.
- `--validate-only`: validate the three required package files without starting Python or showing UI. Exit code `0` means valid and `2` means incomplete.

The launcher passes `--asset-root`, `--data-root`, `--port 8767`, and `--model-profile primary` to the packaged application. It accepts health only for the promoted Baiweixi model and Adapter identities.

Port 8767 is protected by one machine-wide launcher mutex. A second release directory is rejected with a visible warning and never reuses another release's service or data. Normal tray Exit sends an authenticated localhost shutdown request and waits up to 15 seconds for Uvicorn cleanup before the Windows process job is closed as a forced fallback.
