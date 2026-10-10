"""路径相关工具函数

迁移来源：tui_agent.py 行 28-199

提供资源路径智能查找、桌面目录定位、保存路径解析等功能。
支持开发模式（脚本目录）和 pip 安装模式（用户主目录）。

本模块无外部依赖，仅使用标准库 os/sys/importlib/pathlib。
"""
import os
import sys
from pathlib import Path

# ════════════════════════════════════════════════════════════════════
# 资源路径智能查找（支持开发模式和 pip 安装模式）
# 查找优先级：1. 包源码根（__file__ 锚定）2. 脚本目录 3. 环境变量 ZEROAI_HOME 4. 用户主目录
# ════════════════════════════════════════════════════════════════════
# 脚本所在目录（兼容源码运行和打包模式）
# 注意：_SCRIPT_DIR 依赖 sys.argv[0]，会随启动方式漂移（python -m、
# 绝对路径、交互式各不相同），故只作次级候选，不能当资源定位的唯一锚点。
try:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
except Exception:
    _SCRIPT_DIR = os.getcwd()

# 包源码根目录：由本文件自身位置锚定（zeroai/core/paths.py → 上溯三级）。
# 与 _SCRIPT_DIR 不同，它不随工作目录或启动方式变化，是开发模式下定位
# libs/、models/ 的可靠锚点。pip 安装场景下该位置没有 libs/，自动落到后续候选。
_PACKAGE_ROOT_DIR = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

_USER_HOME = os.path.expanduser("~")
_ZEROAI_USER_DIR = os.path.join(_USER_HOME, ".zeroai")

# 桌面目录缓存（兼容中英文 Windows / macOS / Linux，首次调用 _get_desktop_dir 时填充）
_DESKTOP_DIR_CACHE: str = ""


def _get_desktop_dir() -> str:
    """获取桌面目录路径（兼容中英文 Windows / macOS / Linux）。

    优先级：
    1. Windows: SHGetFolderPathW（CSIDL_DESKTOP，最可靠，能识别"桌面"重定向）
    2. Windows: USERPROFILE/Desktop 或 USERPROFILE/桌面
    3. macOS/Linux: ~/Desktop 或 ~/桌面
    4. Linux: XDG_DESKTOP_DIR 环境变量
    5. 回退: 用户主目录
    """
    global _DESKTOP_DIR_CACHE
    if _DESKTOP_DIR_CACHE:
        return _DESKTOP_DIR_CACHE

    # Windows: 用 SHGetFolderPathW 获取真实桌面路径
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_DESKTOP = 0x00
            if ctypes.windll.shell32.SHGetFolderPathW(0, 0x00, 0, 0, buf) == 0:
                if buf.value and os.path.isdir(buf.value):
                    _DESKTOP_DIR_CACHE = buf.value
                    return _DESKTOP_DIR_CACHE
        except Exception:
            pass
        # 回退：USERPROFILE 下找 Desktop / 桌面
        up = os.environ.get("USERPROFILE") or _USER_HOME
        for name in ("Desktop", "桌面"):
            p = os.path.join(up, name)
            if os.path.isdir(p):
                _DESKTOP_DIR_CACHE = p
                return _DESKTOP_DIR_CACHE

    # macOS / Linux
    for name in ("Desktop", "桌面"):
        p = os.path.join(_USER_HOME, name)
        if os.path.isdir(p):
            _DESKTOP_DIR_CACHE = p
            return _DESKTOP_DIR_CACHE

    # Linux XDG
    xdg = os.environ.get("XDG_DESKTOP_DIR")
    if xdg and os.path.isdir(xdg):
        _DESKTOP_DIR_CACHE = xdg
        return _DESKTOP_DIR_CACHE

    # 最终回退：用户主目录
    _DESKTOP_DIR_CACHE = _USER_HOME
    return _DESKTOP_DIR_CACHE


def _resolve_save_path(path: str, default_filename: str) -> str:
    """解析文档保存路径（默认保存到桌面）。

    规则：
    - path 为空：保存到 桌面/{default_filename}
    - path 只含文件名（无目录分隔符）：保存到 桌面/{path}
    - path 含完整路径：按 path 原样保存

    Args:
        path: 用户传入的路径
        default_filename: 默认文件名（如 "未命名.docx"）

    Returns:
        解析后的绝对路径
    """
    if not path:
        return os.path.join(_get_desktop_dir(), default_filename)
    # 判断是否只含文件名（无目录部分）
    if not os.path.dirname(path):
        return os.path.join(_get_desktop_dir(), path)
    return path


def _find_resource_dir(subdir: str) -> str:
    """智能查找资源目录（libs/、models/ 等），支持多位置查找。

    查找优先级：
    1. 包源码根目录的子目录（__file__ 锚定，开发模式：D:\\C\\C\\libs）
    2. 脚本所在目录的子目录（兼容旧行为）
    3. 环境变量 ZEROAI_HOME 指定的子目录（未设置则跳过）
    4. 用户主目录 ~/.zeroai/ 的子目录（pip 安装模式）

    返回第一个存在的绝对目录；若都不存在，返回包源码根的子目录（用于错误提示）。

    环境变量候选必须在未设置时跳过：否则 ``os.path.join("", subdir)`` 会得到
    相对路径 ``"libs"``，只要进程工作目录下恰好有同名目录就会被误匹配，使调用方
    拿到依赖 CWD 的结果。修复前实测即为此现象（从非项目根启动会定位失败），
    故此处再加 ``os.path.isabs`` 作兜底校验。
    """
    _zeroai_home = os.environ.get("ZEROAI_HOME", "")
    candidates = [
        os.path.join(_PACKAGE_ROOT_DIR, subdir),                     # 1. __file__ 锚定
        os.path.join(_SCRIPT_DIR, subdir),                           # 2. 脚本目录（兼容）
        os.path.join(_zeroai_home, subdir) if _zeroai_home else "",  # 3. 环境变量
        os.path.join(_ZEROAI_USER_DIR, subdir),                      # 4. pip 安装模式
    ]
    for p in candidates:
        if p and os.path.isabs(p) and os.path.isdir(p):
            return p
    # 默认返回包源码根的子目录（用于错误提示和后续创建）
    return os.path.join(_PACKAGE_ROOT_DIR, subdir)


def _ensure_vendored_path() -> str:
    """把 vendored 库目录 ``libs/`` **追加到 sys.path 末尾**（只做一次），返回该目录。

    为什么需要它：``libs/`` 下是整套 vendored 依赖（sherpa_onnx 主 ASR 路径、
    faster_whisper 回退路径及其原生依赖 av/ctranslate2 都在其中），但它们从未
    被放进 import 搜索路径，导致 ``import sherpa_onnx`` 抛 ModuleNotFoundError，
    注册好的语音工具实际不可用。

    **只能 append，绝不能 insert 到首位。** 2026-10-10 实测：``libs/`` 含 24 个包，
    若置于 sys.path 首位则其中 **19 个会遮蔽已安装版本**——包括 anyio
    （asyncssh 的依赖，被替换会连带打断 SSH 功能）、httpx、yaml、packaging、
    tokenizers、onnxruntime。而追加到末尾时，实测 10 个既有模块全部仍解析到
    原位置、回归 0 个：site-packages 排在前面永远优先，本函数因此是**严格增量**的
    （只让原本导入失败的名字变得可导入，不改变任何既有解析结果）。

    调用时机：按需调用（用到语音功能前），避免进程启动即改动 sys.path。

    Returns:
        追加的 ``libs`` 目录绝对路径；目录不存在时返回空字符串且不改动 sys.path。
    """
    libs_dir = _find_resource_dir("libs")
    if not libs_dir or not os.path.isdir(libs_dir):
        return ""
    if libs_dir not in sys.path:
        sys.path.append(libs_dir)
    return libs_dir


def _ensure_user_dir() -> str:
    """确保用户主目录 ~/.zeroai/ 存在，返回路径"""
    if not os.path.isdir(_ZEROAI_USER_DIR):
        try:
            os.makedirs(_ZEROAI_USER_DIR, exist_ok=True)
        except OSError:
            pass
    return _ZEROAI_USER_DIR


# ====== 配置文件路径 ======
CONFIG_FILE = Path.home() / ".zeroai_config.json"
CUSTOM_MODELS_FILE = Path.home() / ".zeroai_models.json"


# ====== 资源路径（打包后从 _MEIPASS 读取，开发时从源码目录读取）======
def _get_resource_dir() -> Path:
    """获取资源目录路径（兼容 PyInstaller 打包和源码运行）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，资源在 _MEIPASS 中
        return Path(sys._MEIPASS) / "assets"
    else:
        # 源码运行：从本文件所在目录回溯到项目根目录的 assets
        # 本文件位于 zeroai/core/paths.py，项目根为上两级
        return Path(__file__).parent.parent.parent / "assets"


ASSETS_DIR = _get_resource_dir()
ICONS_DIR = ASSETS_DIR / "icons"


# ====== 工作目录（单一真源）======
# 语义：进程启动时的工作目录快照。
#
# 【为什么放在这里】此前 `WORK_DIR = os.getcwd()` 在三个模块里各写了一遍：
#   - tui_agent.py:1961
#   - zeroai/tools/command_exec.py:32
#   - zeroai/tools/security.py:29
# 三份独立副本的问题不是"重复"本身，而是**求值时机可能不同**：
# 若某个模块被延迟导入（import 发生在用户 chdir 之后），它的 WORK_DIR
# 就会指向另一个目录，而其余两份仍指向启动目录 —— 于是"展示给用户的工作目录"
# 与"实际执行命令的工作目录"可能不一致，且没有任何报错。
#
# 因此统一到本模块：本模块处于导入链最上游，求值时机最早、最稳定。
# 需要"启动时工作目录"语义的地方都应从这里导入，不要再自行 getcwd()。
WORK_DIR = os.getcwd()
