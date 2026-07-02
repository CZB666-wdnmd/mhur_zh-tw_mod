# install_herovs_mod.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path
from typing import Any


# ============================================================
# 需要你在打包前硬编码修改的配置
# ============================================================

GAME_LANGUAGE_TO_SET = "japanese"

TARGET_APP_ID = "1607250"

# 推荐使用 appmanifest_1607250.acf 里的 "buildid"。
# 先运行：
#   python install_herovs_mod.py --print-current-version
# 查看当前安装版本，然后把 buildid 填到这里。
EXPECTED_BUILD_ID = "23523425"

# 如果你想更严格校验 depot manifest，可以在这里填：
# {
#     "1607251": "1234567890123456789",
#     "1607252": "9876543210987654321",
# }
# 不需要则保持空 dict。
EXPECTED_DEPOT_MANIFESTS: dict[str, str] = {}

# 目标位置原本存在的 master.db 的 MD5。
# 只有当前用户 AppData 下原 master.db 匹配这个 MD5，才允许覆盖。
# 手动指定 --mods-dir 和 --master-db-target 时跳过此检查。
EXPECTED_EXISTING_MASTER_DB_MD5 = "423fac124fcdc1ca26625f831660ad86"

PAK_FILENAME = "X001ZHTW-WindowsNoEditor_P.pak"
MASTER_DB_FILENAME = "master.db"

RELATIVE_MODS_DIR = Path("HerovsGame") / "Content" / "Paks" / "Mods"

RELATIVE_MASTER_DB_TARGET = (
    Path("HerovsGame")
    / "Saved"
    / "PersistentDownloadDir"
    / "master.db"
)

LAUNCH_OPTION_TO_ADD = "-fileopenlog"


# ============================================================
# 异常和通用工具
# ============================================================

class InstallError(Exception):
    pass


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_candidates(filename: str) -> list[Path]:
    candidates = [
        Path.cwd() / filename,
        exe_dir() / filename,
    ]

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / filename)

    result: list[Path] = []
    seen: set[str] = set()

    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            result.append(p)

    return result


def find_resource_file(filename: str) -> Path:
    for p in resource_candidates(filename):
        if p.is_file():
            return p.resolve()

    checked = "\n".join(f"  - {p}" for p in resource_candidates(filename))
    raise InstallError(
        f"找不到随包文件：{filename}\n"
        f"请把它放在 exe 同目录，或用 PyInstaller --add-data 打包。\n"
        f"已检查：\n{checked}"
    )


def expand_path(s: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(s))).resolve()


def md5_file(path: Path) -> str:
    h = hashlib.md5()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.name + f".bak_{ts}")
    shutil.copy2(path, backup)
    return backup


def copy_file(src: Path, dst: Path, dry_run: bool = False) -> None:
    print(f"[COPY] {src}")
    print(f"   ->  {dst}")

    if dry_run:
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def read_text_guess(path: Path) -> tuple[str, str]:
    data = path.read_bytes()

    for enc in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass

    return data.decode("mbcs", errors="replace"), "mbcs"


def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    if encoding.lower() == "mbcs":
        encoding = "utf-8"

    path.write_text(text, encoding=encoding, newline="\n")


def steam_running() -> bool:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe"],
            text=True,
            encoding="mbcs",
            errors="ignore",
        )
        return "steam.exe" in out.lower()
    except Exception:
        return False


# ============================================================
# 简易 VDF Parser / Dumper
# 不依赖第三方库，适合 PyInstaller 单文件打包
# ============================================================

class VDFParser:
    def __init__(self, text: str):
        self.tokens = self._tokenize(text)
        self.pos = 0

    def _tokenize(self, text: str) -> list[str]:
        tokens: list[str] = []
        i = 0
        n = len(text)

        while i < n:
            c = text[i]

            if c.isspace():
                i += 1
                continue

            if c == "/" and i + 1 < n and text[i + 1] == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue

            if c in "{}":
                tokens.append(c)
                i += 1
                continue

            if c == '"':
                i += 1
                buf: list[str] = []

                while i < n:
                    c = text[i]

                    if c == '"':
                        i += 1
                        break

                    if c == "\\" and i + 1 < n:
                        nxt = text[i + 1]
                        if nxt in ['"', "\\"]:
                            buf.append(nxt)
                            i += 2
                            continue

                    buf.append(c)
                    i += 1

                tokens.append("".join(buf))
                continue

            start = i
            while i < n and not text[i].isspace() and text[i] not in "{}":
                i += 1

            tokens.append(text[start:i])

        return tokens

    def peek(self) -> str | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def pop(self) -> str:
        if self.pos >= len(self.tokens):
            raise ValueError("VDF 格式错误：意外结束")

        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse(self) -> dict[str, Any]:
        return self._parse_object(until_closing_brace=False)

    def _parse_object(self, until_closing_brace: bool) -> dict[str, Any]:
        obj: dict[str, Any] = {}

        while self.peek() is not None:
            if self.peek() == "}":
                if until_closing_brace:
                    self.pop()
                    return obj
                raise ValueError("VDF 格式错误：多余的 }")

            key = self.pop()

            if self.peek() == "{":
                self.pop()
                obj[key] = self._parse_object(until_closing_brace=True)
            else:
                value = self.pop()
                obj[key] = value

        if until_closing_brace:
            raise ValueError("VDF 格式错误：缺少 }")

        return obj


def parse_vdf_text(text: str) -> dict[str, Any]:
    return VDFParser(text).parse()


def vdf_quote(value: Any) -> str:
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return f'"{s}"'


def dump_vdf(obj: dict[str, Any], indent: int = 0) -> str:
    lines: list[str] = []
    pad = "\t" * indent

    for key, value in obj.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{vdf_quote(key)}")
            lines.append(f"{pad}" + "{")
            lines.append(dump_vdf(value, indent + 1).rstrip("\n"))
            lines.append(f"{pad}" + "}")
        else:
            lines.append(f"{pad}{vdf_quote(key)}\t\t{vdf_quote(value)}")

    return "\n".join(lines) + "\n"


def load_vdf(path: Path) -> tuple[dict[str, Any], str]:
    text, enc = read_text_guess(path)
    return parse_vdf_text(text), enc


# ============================================================
# Steam 路径 / appmanifest / 游戏路径
# ============================================================
def set_appmanifest_language(
    manifest_path: Path,
    language_code: str,
    dry_run: bool = False,
) -> bool:
    """
    修改 Steam 单个游戏语言：
    appmanifest_<appid>.acf / AppState / UserConfig / language
    同时同步 MountedConfig / language。
    """
    if steam_running():
        print()
        print("[WARN] 检测到 Steam 正在运行。")
        print("       修改游戏语言需要写入 appmanifest，Steam 运行时可能会覆盖该文件。")
        print("       请完全退出 Steam 客户端，包括右下角托盘里的 Steam。")
        print("       关闭后按回车继续检测。")
        print("       如果直接按回车但 Steam 仍未关闭，本程序将跳过自动设置游戏语言。")
        input("按回车继续...")

        if steam_running():
            print("[WARN] Steam 仍在运行，已跳过自动设置游戏语言。")
            print(f"       请稍后手动设置：Steam -> 游戏属性 -> 通用 -> 语言 -> 日本语")
            return False

    data, encoding = load_vdf(manifest_path)

    app_state = data.get("AppState")
    if app_state is None:
        app_state = data
    if not isinstance(app_state, dict):
        raise InstallError(f"appmanifest 格式异常：{manifest_path}")

    user_config = app_state.get("UserConfig")
    if not isinstance(user_config, dict):
        user_config = {}
        app_state["UserConfig"] = user_config

    mounted_config = app_state.get("MountedConfig")
    if not isinstance(mounted_config, dict):
        mounted_config = {}
        app_state["MountedConfig"] = mounted_config

    old_user_language = str(user_config.get("language", ""))
    old_mounted_language = str(mounted_config.get("language", ""))

    user_config["language"] = language_code
    mounted_config["language"] = language_code

    if old_user_language == language_code and old_mounted_language == language_code:
        print(f"[OK] Steam 游戏语言已是：{language_code}")
        return False

    print("[VDF] 准备写入 Steam 游戏语言：")
    print(f"      文件：{manifest_path}")
    print(f"      AppState/UserConfig/language = {language_code}")
    print(f"      AppState/MountedConfig/language = {language_code}")

    if dry_run:
        return True

    backup = backup_file(manifest_path)
    if backup:
        print(f"[BACKUP] {backup}")

    write_text(manifest_path, dump_vdf(data), encoding)
    print("[OK] 已写入 Steam 游戏语言。")
    return True


def read_reg_string(root, subkey: str, name: str) -> str | None:
    flags_list = [0]

    if hasattr(winreg, "KEY_WOW64_32KEY"):
        flags_list.append(winreg.KEY_WOW64_32KEY)

    for flags in flags_list:
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | flags) as k:
                value, _ = winreg.QueryValueEx(k, name)
                if value:
                    return str(value)
        except OSError:
            pass

    return None


def find_steam_root(override: str | None = None) -> Path:
    if override:
        p = expand_path(override)
        if not p.exists():
            raise InstallError(f"--steam-root 指定的路径不存在：{p}")
        return p

    candidates: list[Path] = []

    registry_items = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]

    for root, subkey, name in registry_items:
        value = read_reg_string(root, subkey, name)
        if value:
            candidates.append(Path(value.replace("/", "\\")))

    candidates.append(Path(r"C:\Program Files (x86)\Steam"))

    for p in candidates:
        if (p / "steam.exe").exists() or (p / "steamapps").exists():
            return p.resolve()

    raise InstallError("找不到 Steam 安装目录，请使用 --steam-root 手动指定。")


def parse_libraryfolders(steam_root: Path) -> list[Path]:
    libraries: list[Path] = [steam_root]
    libraryfolders = steam_root / "steamapps" / "libraryfolders.vdf"

    if libraryfolders.exists():
        try:
            data, _ = load_vdf(libraryfolders)
            root = data.get("libraryfolders", data)

            if isinstance(root, dict):
                for _, value in root.items():
                    if isinstance(value, dict):
                        path_value = value.get("path")
                        if path_value:
                            libraries.append(Path(str(path_value)))
                    elif isinstance(value, str):
                        if ":" in value or value.startswith("\\\\"):
                            libraries.append(Path(value))
        except Exception as e:
            print(f"[WARN] 解析 libraryfolders.vdf 失败，将只检查默认库。原因：{e}")

    result: list[Path] = []
    seen: set[str] = set()

    for p in libraries:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            result.append(p)

    return result


def find_appmanifest(steam_root: Path, appid: str) -> Path:
    for library in parse_libraryfolders(steam_root):
        manifest = library / "steamapps" / f"appmanifest_{appid}.acf"
        if manifest.exists():
            return manifest.resolve()

    raise InstallError(f"未找到 appmanifest_{appid}.acf，请确认游戏已通过 Steam 安装。")


def app_state_from_manifest(manifest_path: Path) -> dict[str, Any]:
    data, _ = load_vdf(manifest_path)
    state = data.get("AppState", data)

    if not isinstance(state, dict):
        raise InstallError(f"appmanifest 格式异常：{manifest_path}")

    return state


def get_depot_manifests(app_state: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    installed = app_state.get("InstalledDepots")

    if isinstance(installed, dict):
        for depot_id, depot_info in installed.items():
            if isinstance(depot_info, dict):
                manifest = depot_info.get("manifest")
                if manifest:
                    result[str(depot_id)] = str(manifest)

    return result


def print_current_version(manifest_path: Path) -> None:
    state = app_state_from_manifest(manifest_path)

    print(f"AppID: {TARGET_APP_ID}")
    print(f"Manifest file: {manifest_path}")
    print(f'buildid: {state.get("buildid", "")}')

    depots = get_depot_manifests(state)
    if depots:
        print("InstalledDepots manifests:")
        for depot_id, manifest_id in sorted(depots.items()):
            print(f"  {depot_id}: {manifest_id}")
    else:
        print("InstalledDepots manifests: <none>")


def validate_expected_version(app_state: dict[str, Any]) -> None:
    if (
        EXPECTED_BUILD_ID == "REPLACE_WITH_EXPECTED_BUILDID"
        and not EXPECTED_DEPOT_MANIFESTS
    ):
        raise InstallError(
            "脚本头部还没有配置 EXPECTED_BUILD_ID 或 EXPECTED_DEPOT_MANIFESTS。\n"
            "请先运行 --print-current-version 查看当前版本，再把期望版本写进脚本头部。"
        )

    if EXPECTED_BUILD_ID != "REPLACE_WITH_EXPECTED_BUILDID":
        actual = str(app_state.get("buildid", ""))
        expected = str(EXPECTED_BUILD_ID)

        if actual != expected:
            raise InstallError(
                "游戏版本不匹配，停止安装。\n"
                f"期望 buildid：{expected}\n"
                f"实际 buildid：{actual}"
            )

    if EXPECTED_DEPOT_MANIFESTS:
        actual_depots = get_depot_manifests(app_state)

        for depot_id, expected_manifest in EXPECTED_DEPOT_MANIFESTS.items():
            actual_manifest = actual_depots.get(str(depot_id))

            if actual_manifest != str(expected_manifest):
                raise InstallError(
                    "Depot manifest 不匹配，停止安装。\n"
                    f"Depot：{depot_id}\n"
                    f"期望：{expected_manifest}\n"
                    f"实际：{actual_manifest}"
                )


def game_dir_from_manifest(manifest_path: Path) -> Path:
    state = app_state_from_manifest(manifest_path)
    install_dir = state.get("installdir")

    if not install_dir:
        raise InstallError(f"appmanifest 里没有 installdir：{manifest_path}")

    library_root = manifest_path.parent.parent
    game_dir = library_root / "steamapps" / "common" / str(install_dir)

    if not game_dir.exists():
        raise InstallError(f"推导出的游戏目录不存在：{game_dir}")

    return game_dir.resolve()


# ============================================================
# master.db 目标文件校验
# ============================================================

def default_master_db_target() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")

    if not local_appdata:
        raise InstallError("找不到 LOCALAPPDATA 环境变量。")

    return Path(local_appdata) / RELATIVE_MASTER_DB_TARGET


def normalize_master_target(path_str: str) -> Path:
    p = expand_path(path_str)

    # 允许传目录：
    # --master-db-target "C:\Users\...\PersistentDownloadDir"
    if p.exists() and p.is_dir():
        return p / MASTER_DB_FILENAME

    # 如果传入路径无后缀，也按目录处理
    if p.suffix == "":
        return p / MASTER_DB_FILENAME

    return p


def validate_existing_master_db_md5(master_target: Path) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", EXPECTED_EXISTING_MASTER_DB_MD5):
        raise InstallError(
            "脚本头部 EXPECTED_EXISTING_MASTER_DB_MD5 未配置为有效的 32 位 MD5。"
        )

    if not master_target.exists():
        raise InstallError(
            "目标位置不存在原 master.db，无法校验版本，停止安装。\n"
            f"目标路径：{master_target}"
        )

    if not master_target.is_file():
        raise InstallError(
            "目标 master.db 路径不是文件，停止安装。\n"
            f"目标路径：{master_target}"
        )

    actual = md5_file(master_target)
    expected = EXPECTED_EXISTING_MASTER_DB_MD5.lower()

    if actual.lower() != expected:
        raise InstallError(
            "目标位置原 master.db MD5 不匹配，停止安装。\n"
            f"文件：{master_target}\n"
            f"期望：{expected}\n"
            f"实际：{actual}"
        )


# ============================================================
# localconfig.vdf 启动参数写入
# 路径固定为：
# UserLocalConfigStore / Software / Valve / Steam / apps / <appid> / LaunchOptions
# 注意这里是小写 apps
# ============================================================

def find_localconfig_files(
    steam_root: Path,
    steam_userdata_id: str | None = None,
) -> list[Path]:
    userdata = steam_root / "userdata"

    if steam_userdata_id:
        path = userdata / steam_userdata_id / "config" / "localconfig.vdf"
        return [path] if path.exists() else []

    if not userdata.exists():
        return []

    files = list(userdata.glob("*/config/localconfig.vdf"))
    files.sort(
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return files


def contains_launch_option(existing: str, option: str) -> bool:
    pattern = r"(^|\s)" + re.escape(option) + r"(\s|$)"
    return re.search(pattern, existing) is not None


def get_steam_apps_object(data: dict[str, Any], create: bool) -> dict[str, Any]:
    def ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
        value = parent.get(key)

        if value is None:
            if not create:
                return {}
            value = {}
            parent[key] = value

        if not isinstance(value, dict):
            if not create:
                return {}
            value = {}
            parent[key] = value

        return value

    root = ensure_dict(data, "UserLocalConfigStore")
    software = ensure_dict(root, "Software")
    valve = ensure_dict(software, "Valve")
    steam = ensure_dict(valve, "Steam")

    # 按你提供的结构，必须使用小写 apps。
    apps = ensure_dict(steam, "apps")

    return apps


def localconfig_has_app(localconfig: Path, appid: str) -> bool:
    try:
        data, _ = load_vdf(localconfig)
        apps = get_steam_apps_object(data, create=False)
        return isinstance(apps, dict) and appid in apps
    except Exception:
        return False


def choose_localconfigs_for_launch_option(
    steam_root: Path,
    appid: str,
    steam_userdata_id: str | None,
) -> list[Path]:
    files = find_localconfig_files(steam_root, steam_userdata_id)

    if steam_userdata_id:
        return files

    # 优先修改已经存在该 AppID 的 localconfig
    with_target_app = [p for p in files if localconfig_has_app(p, appid)]
    if with_target_app:
        return with_target_app

    # 找不到该 AppID 时，修改最近使用的一个用户配置
    return files[:1]


def add_launch_option_to_localconfig(
    localconfig: Path,
    appid: str,
    option: str,
    dry_run: bool = False,
) -> bool:
    data, encoding = load_vdf(localconfig)

    apps = get_steam_apps_object(data, create=True)

    app_cfg = apps.get(appid)
    if app_cfg is None:
        app_cfg = {}
        apps[appid] = app_cfg

    if not isinstance(app_cfg, dict):
        app_cfg = {}
        apps[appid] = app_cfg

    existing = str(app_cfg.get("LaunchOptions", "")).strip()

    if contains_launch_option(existing, option):
        print(f"[OK] 启动参数已存在：{localconfig}")
        print(f"     LaunchOptions = {existing}")
        return False

    new_value = f"{existing} {option}".strip() if existing else option
    app_cfg["LaunchOptions"] = new_value

    print("[VDF] 准备写入启动参数：")
    print(f"      文件：{localconfig}")
    print(f"      路径：UserLocalConfigStore/Software/Valve/Steam/apps/{appid}/LaunchOptions")
    print(f"      值：{new_value}")

    if dry_run:
        return True

    backup = backup_file(localconfig)
    if backup:
        print(f"[BACKUP] {backup}")

    write_text(localconfig, dump_vdf(data), encoding)
    return True


def try_add_launch_option(
    steam_root: Path,
    appid: str,
    option: str,
    steam_userdata_id: str | None = None,
    dry_run: bool = False,
) -> bool:
    try:
        files = choose_localconfigs_for_launch_option(
            steam_root=steam_root,
            appid=appid,
            steam_userdata_id=steam_userdata_id,
        )

        if not files:
            raise InstallError("未找到 userdata\\*\\config\\localconfig.vdf")

        if steam_running():
            print()
            print("[WARN] 检测到 Steam 正在运行。")
            print("       请完全退出 Steam 客户端，包括右下角托盘里的 Steam。")
            print("       关闭后按回车继续检测。")
            print("       如果直接按回车但 Steam 仍未关闭，本程序将跳过自动写入启动参数。")
            input("按回车继续...")

            if steam_running():
                print("[WARN] Steam 仍在运行，已跳过自动写入 localconfig.vdf。")
                print(f"       请稍后手动给游戏 {appid} 添加启动参数：{option}")
                return False

        changed_any = False

        for localconfig in files:
            changed = add_launch_option_to_localconfig(
                localconfig=localconfig,
                appid=appid,
                option=option,
                dry_run=dry_run,
            )
            changed_any = changed_any or changed

        if changed_any:
            print("[OK] 已写入 Steam 启动参数。")
        else:
            print("[OK] 启动参数已存在，无需重复写入。")

        return True

    except Exception as e:
        print("[WARN] 自动添加 Steam 启动参数失败，但 Mod 文件安装不受影响。")
        print(f"       原因：{e}")
        print(f"       请手动给游戏 {appid} 添加启动参数：{option}")
        return False


# ============================================================
# 安装流程
# ============================================================

def install(args: argparse.Namespace) -> int:
    dry_run = args.dry_run

    manual_mode = bool(args.mods_dir or args.master_db_target)

    if manual_mode and not (args.mods_dir and args.master_db_target):
        raise InstallError(
            "手动路径模式需要同时指定：\n"
            "  --mods-dir <Mods文件夹路径>\n"
            "  --master-db-target <master.db目标路径或目录>"
        )

    steam_root: Path | None = None

    # --print-current-version 不要求 pak/master.db 存在
    if args.print_current_version:
        steam_root = find_steam_root(args.steam_root)
        manifest_path = find_appmanifest(steam_root, TARGET_APP_ID)
        print_current_version(manifest_path)
        return 0

    pak_src = find_resource_file(PAK_FILENAME)
    master_src = find_resource_file(MASTER_DB_FILENAME)

    if args.steam_root or not manual_mode or not args.skip_launch_options:
        try:
            steam_root = find_steam_root(args.steam_root)
            print(f"[INFO] SteamRoot = {steam_root}")
        except Exception as e:
            if not manual_mode:
                raise
            print(f"[WARN] 无法自动定位 Steam：{e}")

    if manual_mode:
        print("[INFO] 手动路径模式：跳过 Steam 版本校验和目标 master.db MD5 校验。")
        mods_dir = expand_path(args.mods_dir)
        master_target = normalize_master_target(args.master_db_target)
    else:
        if steam_root is None:
            steam_root = find_steam_root(args.steam_root)

        manifest_path = find_appmanifest(steam_root, TARGET_APP_ID)
        print(f"[INFO] AppManifest = {manifest_path}")

        app_state = app_state_from_manifest(manifest_path)
        validate_expected_version(app_state)
        print("[OK] 游戏安装版本校验通过。")

        game_dir = game_dir_from_manifest(manifest_path)
        print(f"[INFO] GameDir = {game_dir}")

        mods_dir = game_dir / RELATIVE_MODS_DIR
        master_target = default_master_db_target()

        validate_existing_master_db_md5(master_target)
        print("[OK] 目标位置原 master.db MD5 校验通过。")

    pak_target = mods_dir / PAK_FILENAME

    print()
    print("[INFO] 安装路径确认：")
    print(f"       Pak source       = {pak_src}")
    print(f"       Pak target       = {pak_target}")
    print(f"       master.db source = {master_src}")
    print(f"       master.db target = {master_target}")
    print()

    if master_target.exists():
        if dry_run:
            print(f"[DRY-RUN] 将备份已有 master.db：{master_target}")
        else:
            backup = backup_file(master_target)
            if backup:
                print(f"[BACKUP] {backup}")

    copy_file(pak_src, pak_target, dry_run=dry_run)
    copy_file(master_src, master_target, dry_run=dry_run)

    print("[OK] Mod 文件安装完成。")

    launch_option_written = False

    if not args.skip_launch_options:
        if steam_root is None:
            try:
                steam_root = find_steam_root(args.steam_root)
            except Exception as e:
                print("[WARN] 无法定位 Steam，跳过自动添加启动参数。")
                print(f"       原因：{e}")
                print(f"       请手动给游戏 {TARGET_APP_ID} 添加启动参数：{LAUNCH_OPTION_TO_ADD}")
            else:
                launch_option_written = try_add_launch_option(
                    steam_root=steam_root,
                    appid=TARGET_APP_ID,
                    option=LAUNCH_OPTION_TO_ADD,
                    steam_userdata_id=args.steam_userdata_id,
                    dry_run=dry_run,
                )
        else:
            launch_option_written = try_add_launch_option(
                steam_root=steam_root,
                appid=TARGET_APP_ID,
                option=LAUNCH_OPTION_TO_ADD,
                steam_userdata_id=args.steam_userdata_id,
                dry_run=dry_run,
            )
    else:
        print(f"[INFO] 已跳过启动参数写入。请手动添加：{LAUNCH_OPTION_TO_ADD}")

    print()
    set_appmanifest_language(
    manifest_path=manifest_path,
    language_code=GAME_LANGUAGE_TO_SET,
    dry_run=dry_run,
    )
    print("[IMPORTANT] 启动参数提醒：")
    if launch_option_written:
        print(f"            已尝试自动添加：{LAUNCH_OPTION_TO_ADD}")
        print("            如果进入游戏后 Mod 没有生效，请在 Steam 游戏属性里确认启动参数是否存在。")
    else:
        print(f"            请手动打开 Steam -> 游戏属性 -> 通用 -> 启动选项，添加：{LAUNCH_OPTION_TO_ADD}")

    print()
    print("[IMPORTANT] 语言设置提醒：")
    print("            已尝试自动设定语言,如果进入游戏后不是中文")
    print("            请在 Steam 游戏属性中将语言设置为「日本语」，以加载对应的中文汉化 Mod。")

    print()
    print("[DONE] 全部流程结束。")
    return 0


# ============================================================
# CLI
# ============================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="一键安装 HerovsGame 中文汉化 Mod，并尝试写入 Steam 启动参数。"
    )

    parser.add_argument(
        "--mods-dir",
        help="手动指定 Mods 文件夹路径。指定后必须同时指定 --master-db-target，并跳过版本/MD5校验。",
    )

    parser.add_argument(
        "--master-db-target",
        help="手动指定 master.db 目标文件路径或 PersistentDownloadDir 目录。指定后必须同时指定 --mods-dir，并跳过版本/MD5校验。",
    )

    parser.add_argument(
        "--steam-root",
        help=r"手动指定 Steam 根目录，例如 C:\Program Files (x86)\Steam。",
    )

    parser.add_argument(
        "--steam-userdata-id",
        help=r"手动指定 Steam userdata 数字目录 ID，例如 123456789。用于写入 localconfig.vdf。",
    )

    parser.add_argument(
        "--print-current-version",
        action="store_true",
        help="只打印当前 appmanifest 里的 buildid/depot manifest，不安装。",
    )

    parser.add_argument(
        "--skip-launch-options",
        action="store_true",
        help="不尝试修改 Steam localconfig.vdf 启动参数。",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：只打印将要执行的操作，不写入文件。",
    )

    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="exe 模式下结束时不暂停。",
    )

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        return install(args)
    except InstallError as e:
        print()
        print("[ERROR] 安装中止。")
        print(e)
        print()
        print("[IMPORTANT] 如需手动设置启动参数：")
        print(f"            Steam -> 游戏属性 -> 通用 -> 启动选项 -> 添加 {LAUNCH_OPTION_TO_ADD}")
        print()
        print("[IMPORTANT] 请在 Steam 游戏属性中将语言设置为「日本语」，以加载对应的中文汉化 Mod。")
        return 1
    except KeyboardInterrupt:
        print()
        print("[ERROR] 用户取消。")
        return 130
    except Exception as e:
        print()
        print("[ERROR] 未预期错误。")
        print(repr(e))
        print()
        print("[IMPORTANT] 如需手动设置启动参数：")
        print(f"            Steam -> 游戏属性 -> 通用 -> 启动选项 -> 添加 {LAUNCH_OPTION_TO_ADD}")
        print()
        print("[IMPORTANT] 请在 Steam 游戏属性中将语言设置为「日本语」，以加载对应的中文汉化 Mod。")
        return 1
    finally:
        if is_frozen() and not getattr(args, "no_pause", False):
            try:
                input("\n按回车退出...")
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
