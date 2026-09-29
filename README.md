# CatGirlfriend Desktop v0.0.1

Windows desktop wallpaper application with a Unity 3D apartment, a separate chat window, and a local Bai Weixi model service. The wallpaper stays behind desktop icons. This repository contains the client, backend, source assets, tests, and design documents.

## Source layout

- `UnityClient/AiGirlFriendUnity`: Unity 6000.1.1f1 project, including the apartment, character, UI, wallpaper shell, and tests.
- `UnityClient/Tools`: build and diagnostic tools.
- `AiPeople/runtime`, `AiPeople/tools`, `AiPeople/portable_launcher`: local service and Windows launcher source.
- `AiPeopleCreate`: training-data generator source and tests.
- `AiPeople/设计文档`, `AiPeopleCreate/设计文档`, `人物设定`, `世界设定`: design and canon source documents.

## Run and build

Open `UnityClient/AiGirlFriendUnity` with Unity 6000.1.1f1. The Windows player is built with the `AiPeople/构建 Windows 播放器` editor menu or `AiPeople.EditorTools.AiPeoplePlayerBuilder.BuildWindowsPlayer` in batch mode. The player expects a matching `Backend` directory beside `CatGirlfriend.exe`; building the Unity project alone does not bundle the model.

The portable backend is assembled with `AiPeople/tools/build_portable_windows.py` from locally supplied model assets and a Python 3.11 runtime. Its launcher source and build instructions are in `AiPeople/portable_launcher/README.md`. See `UnityClient/README.md` for client setup and `AiPeople/README.md` for backend setup. Model manifests identify the exact base and Adapter files used by the tested build.

The 2026-09-29 desktop candidate adds per-fact source, version, and current/unknown/stale observations for doors, windows, lights, and movement. Backend contract tests and Unity PlayMode tests pass. A local full-package test confirmed automatic backend launch, wallpaper attachment, a real chat turn through Unity, and clean exit; second-machine acceptance of this updated build is still pending. The earlier PortableV1 build passed second-machine private testing. These are different builds and their client/backend files must not be mixed.

## Distribution boundary

Model weights, Adapter weights, Python runtime, databases, build output, and test caches are not source files and are not in Git. The portable release manifest currently lists missing upstream Gemma, BGE, Qwen, and Adapter license materials. Until redistribution terms and notices are resolved, this repository is a source release rather than a public, ready-to-download model bundle. The existing backend `v0.0.1` tag refers to an older September 2 source baseline; this desktop release uses a separate version name.
