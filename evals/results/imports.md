# 第三方依赖声明审计

- 扫描文件数: 88
- pyproject 已声明（含 extras）: 28 个
- 实际 import 的第三方模块: 25 个
- **已 import 但未声明: 0 个**

## 未声明的依赖

| import 名 | PyPI 发行名 | 引用文件数 | 引用位置 |
|---|---|---|---|

## pyproject 现有声明分组

- **optional:all**（13）: av, edge-tts, faiss-cpu, faster-whisper, httpx, pygame, send2trash, sherpa-onnx, sounddevice, starlette, uiautomation, uvicorn, watchdog
- **optional:desktop**（1）: uiautomation
- **optional:dev**（2）: build, pyinstaller
- **optional:files**（1）: send2trash
- **optional:mcp**（3）: httpx, starlette, uvicorn
- **optional:vector**（2）: faiss-cpu, watchdog
- **optional:voice**（6）: av, edge-tts, faster-whisper, pygame, sherpa-onnx, sounddevice
- **project**（13）: Pillow, asyncssh, matplotlib, numpy, openai, openpyxl, psutil, python-docx, pyyaml, reportlab, requests, rich, textual

## 已声明且未被 import 的（疑似过期）

`av`, `build`, `pyinstaller`, `requests`
