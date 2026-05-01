from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


APP_DIR = Path(__file__).resolve().parent
ACCOUNTS_DIR = APP_DIR / "accounts"
CODEX_DIR = Path.home() / ".codex"
TARGET_AUTH = CODEX_DIR / "auth.json"
BACKUP_DIR = APP_DIR / "backups"
ACCOUNT_META_FILE = ACCOUNTS_DIR / "account-meta.json"
ACCOUNT_PREFIX = "auth -"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
LOCAL_PROXY_ENV = "CODEX_ACC_SWITCH_PROXY"


@dataclass(frozen=True)
class AccountFile:
    label: str
    display_label: str
    path: Path
    digest: str
    size: int
    modified: datetime


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_json_file(path: Path) -> None:
    with path.open("r", encoding="utf-8") as handle:
        json.load(handle)


def label_for(path: Path) -> str:
    stem = path.stem.strip()
    if stem.lower().startswith("auth"):
        stem = stem[4:].strip(" -_")
    return stem or path.stem


def iter_candidate_paths() -> Iterable[Path]:
    seen: set[Path] = set()
    for path in sorted(ACCOUNTS_DIR.glob("auth*.json")):
        if path.name.lower() == "auth.json":
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield path


def discover_accounts() -> list[AccountFile]:
    accounts: list[AccountFile] = []
    for path in iter_candidate_paths():
        validate_json_file(path)
        stat = path.stat()
        label = label_for(path)
        accounts.append(
            AccountFile(
                label=label,
                display_label=label,
                path=path,
                digest=sha256_file(path),
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )
        )
    return accounts


def current_digest() -> str | None:
    if not TARGET_AUTH.exists():
        return None
    return sha256_file(TARGET_AUTH)


def active_account(accounts: list[AccountFile]) -> AccountFile | None:
    digest = current_digest()
    if digest is None:
        return None
    for account in accounts:
        if account.digest == digest:
            return account
    return None


def make_backup() -> Path | None:
    if not TARGET_AUTH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"auth.backup-{stamp}.json"
    shutil.copy2(TARGET_AUTH, backup_path)
    return backup_path


def make_account_backup(path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"{path.stem}.backup-{stamp}{path.suffix}"
    shutil.copy2(path, backup_path)
    return backup_path


def switch_to(account: AccountFile) -> Path | None:
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    validate_json_file(account.path)
    backup_path = make_backup()
    shutil.copy2(account.path, TARGET_AUTH)
    return backup_path


def copy_current_auth_to(target: Path) -> Path | None:
    if not TARGET_AUTH.exists():
        raise FileNotFoundError(f"Current auth.json was not found: {TARGET_AUTH}")
    validate_json_file(TARGET_AUTH)
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_path = make_account_backup(target) if target.exists() else None
    shutil.copy2(TARGET_AUTH, target)
    return backup_path


def clean_account_label(label: str) -> str:
    cleaned = "".join(char for char in label.strip() if char not in '<>:"/\\|?*')
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        raise ValueError("Account label is required.")
    return cleaned


def account_path_for_label(label: str) -> Path:
    cleaned = clean_account_label(label)
    return ACCOUNTS_DIR / f"{ACCOUNT_PREFIX}{cleaned}.json"


def validate_json_text(raw_json: str) -> str:
    parsed = json.loads(raw_json)
    return json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"


def load_account_meta() -> dict[str, Any]:
    if not ACCOUNT_META_FILE.exists():
        return {"accounts": {}}
    try:
        with ACCOUNT_META_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("accounts"), dict):
            return data
    except Exception:
        pass
    return {"accounts": {}}


def save_account_meta(data: dict[str, Any]) -> None:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    with ACCOUNT_META_FILE.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def account_meta_key(path: Path) -> str:
    return path.name


def get_cached_usage(path: Path) -> dict[str, Any] | None:
    data = load_account_meta()
    account_data = data.get("accounts", {}).get(account_meta_key(path), {})
    if isinstance(account_data, dict):
        usage = account_data.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


def set_cached_usage(path: Path, usage: dict[str, Any]) -> None:
    data = load_account_meta()
    accounts = data.setdefault("accounts", {})
    if not isinstance(accounts, dict):
        accounts = {}
        data["accounts"] = accounts
    key = account_meta_key(path)
    item = accounts.setdefault(key, {})
    if not isinstance(item, dict):
        item = {}
        accounts[key] = item
    item["usage"] = usage
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_account_meta(data)


def move_account_meta(old_path: Path, new_path: Path) -> None:
    data = load_account_meta()
    accounts = data.get("accounts", {})
    if not isinstance(accounts, dict):
        return
    old_key = account_meta_key(old_path)
    new_key = account_meta_key(new_path)
    if old_key in accounts:
        accounts[new_key] = accounts.pop(old_key)
        save_account_meta(data)


def auth_last_refresh(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        value = data.get("last_refresh", "")
        return str(value) if value else "-"
    except Exception:
        return "-"


def parse_rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_codex_token_stale(last_refresh: str) -> bool:
    refreshed_at = parse_rfc3339(last_refresh)
    if not refreshed_at:
        return False
    return (datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc)).total_seconds() > 8 * 24 * 3600


def query_codex_usage(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        auth = json.load(handle)

    if auth.get("auth_mode") != "chatgpt":
        raise ValueError("This auth file is not in Codex ChatGPT OAuth mode.")

    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        raise ValueError("No tokens object found in auth file.")

    access_token = tokens.get("access_token")
    if not access_token:
        raise ValueError("No access_token found in auth file.")

    account_id = tokens.get("account_id")
    last_refresh = auth.get("last_refresh")
    stale = bool(last_refresh and is_codex_token_stale(str(last_refresh)))

    try:
        body = request_codex_usage(access_token, account_id, proxy_url=None)
        proxy_used = None
    except ValueError:
        raise
    except Exception as direct_exc:
        last_exc = direct_exc
        for proxy_url in proxy_candidates():
            try:
                body = request_codex_usage(access_token, account_id, proxy_url=proxy_url)
                proxy_used = proxy_url
                break
            except Exception as proxy_exc:
                last_exc = proxy_exc
        else:
            if isinstance(last_exc, urllib.error.URLError):
                raise ValueError(f"Network error while querying usage: {last_exc.reason}") from last_exc
            raise ValueError(f"Network error while querying usage: {last_exc}") from last_exc

    rate_limit = body.get("rate_limit") if isinstance(body, dict) else None
    tiers: list[dict[str, Any]] = []
    if isinstance(rate_limit, dict):
        for key in ("primary_window", "secondary_window"):
            window = rate_limit.get(key)
            if not isinstance(window, dict):
                continue
            used = window.get("used_percent")
            if used is None:
                continue
            tiers.append(
                {
                    "name": window_seconds_to_tier_name(window.get("limit_window_seconds")),
                    "used_percent": float(used),
                    "remaining_percent": max(0.0, 100.0 - float(used)),
                    "reset_at": unix_ts_to_local_text(window.get("reset_at")),
                }
            )

    return {
        "success": True,
        "queried_at": datetime.now().isoformat(timespec="seconds"),
        "last_refresh": str(last_refresh) if last_refresh else "",
        "token_stale": stale,
        "proxy": mask_proxy_url(proxy_used) if proxy_used else "direct",
        "tiers": tiers,
    }


def request_codex_usage(access_token: str, account_id: Any, proxy_url: str | None) -> dict[str, Any]:
    request = urllib.request.Request(
        CODEX_USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "codex-cli",
            "Accept": "application/json",
        },
        method="GET",
    )
    if account_id:
        request.add_header("ChatGPT-Account-Id", str(account_id))

    try:
        opener = urllib.request.build_opener()
        if proxy_url:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
        with opener.open(request, timeout=15) as response:
            status = response.status
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {401, 403}:
            raise ValueError(f"Authentication failed (HTTP {exc.code}). Please re-login this account.") from exc
        raise ValueError(f"Usage API error (HTTP {exc.code}): {body}") from exc

    if status < 200 or status >= 300:
        raise ValueError(f"Usage API error (HTTP {status}): {raw}")

    return json.loads(raw)


def proxy_candidates() -> list[str]:
    values: list[str] = []
    env_proxy = os_environ(LOCAL_PROXY_ENV)
    if env_proxy:
        values.append(env_proxy)
    values.extend(read_windows_proxy_servers())
    normalized: list[str] = []
    for value in values:
        url = normalize_proxy_url(value)
        if url and url not in normalized:
            normalized.append(url)
    return normalized


def os_environ(name: str) -> str:
    try:
        import os

        return os.environ.get(name, "").strip()
    except Exception:
        return ""


def read_windows_proxy_servers() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            value, _kind = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()]


def normalize_proxy_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "=" in value:
        scheme, raw = value.split("=", 1)
        if scheme.lower() not in {"http", "https", "socks", "socks5"}:
            return ""
        value = raw.strip()
    if not value.startswith(("http://", "https://", "socks5://", "socks5h://")):
        value = f"http://{value}"
    return value


def mask_proxy_url(value: str | None) -> str:
    if not value:
        return "direct"
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    except Exception:
        return value


def window_seconds_to_tier_name(seconds: Any) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if seconds == 18000:
        return "5h"
    if seconds == 604800:
        return "7d"
    hours = seconds // 3600
    if hours >= 24:
        return f"{hours // 24}d"
    return f"{hours}h"


def unix_ts_to_local_text(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def format_usage_summary(usage: dict[str, Any] | None) -> str:
    if not usage:
        return "未刷新"
    if not usage.get("success"):
        return str(usage.get("error") or "查询失败")
    tiers = usage.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        return "已刷新，但没有返回额度窗口"
    parts = []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        name = tier.get("name", "unknown")
        used = float(tier.get("used_percent", 0.0))
        remaining = float(tier.get("remaining_percent", max(0.0, 100.0 - used)))
        reset_at = tier.get("reset_at") or "-"
        parts.append(f"{name}: 剩余 {remaining:.1f}% / 重置 {reset_at}")
    suffix = ""
    if usage.get("token_stale"):
        suffix = "；token 可能需要重新登录刷新"
    return "；".join(parts) + suffix


def account_table(accounts: list[AccountFile]) -> str:
    active = active_account(accounts)
    rows = []
    for index, account in enumerate(accounts, start=1):
        marker = "*" if active and account.path == active.path else " "
        rows.append(
            f"{marker} {index}. {account.display_label}  "
            f"({account.path}, {account.size} bytes, "
            f"{account.modified:%Y-%m-%d %H:%M:%S})"
        )
    return "\n".join(rows)


def find_account(accounts: list[AccountFile], selector: str) -> AccountFile:
    lowered = selector.casefold()
    for index, account in enumerate(accounts, start=1):
        if selector == str(index):
            return account
        if lowered in {
            account.label.casefold(),
            account.display_label.casefold(),
            account.path.name.casefold(),
            account.path.stem.casefold(),
        }:
            return account
    raise SystemExit(f"Account not found: {selector}")


def run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Switch Codex auth.json between local account files.")
    parser.add_argument("--list", action="store_true", help="List detected account files.")
    parser.add_argument("--use", metavar="ACCOUNT", help="Switch by number, label, or filename.")
    args = parser.parse_args(argv)

    accounts = discover_accounts()
    if not accounts:
        raise SystemExit("No account files found. Add auth-*.json files next to this script.")

    if args.list or not args.use:
        print(account_table(accounts))

    if args.use:
        account = find_account(accounts, args.use)
        backup_path = switch_to(account)
        print(f"Switched to: {account.display_label}")
        if backup_path:
            print(f"Backup: {backup_path}")
    return 0


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk
    except Exception as exc:  # pragma: no cover - depends on local Python install
        print(f"Unable to load Tkinter: {exc}", file=sys.stderr)
        return 1

    class Theme:
        bg = "#0a0a0a"
        sidebar = "#111111"
        panel = "#151515"
        panel_alt = "#1f1f23"
        hover = "#1a1a1a"
        selected = "#27272a"
        border = "#2d2d32"
        text = "#fafafa"
        subtext = "#d4d4d8"
        muted = "#85858f"
        dim = "#5f5f68"
        accent = "#6366f1"
        success = "#10b981"
        danger = "#f87171"
        primary_bg = "#fafafa"
        primary_fg = "#18181b"
        font = "Segoe UI"
        mono = "Cascadia Code"

    class AccountSwitcherApp:
        def __init__(self, root_window: tk.Tk):
            self.root = root_window
            self.root.title("Codex Account Switcher")
            self.root.configure(bg=Theme.bg)
            self.root.geometry("1220x760")
            self.root.minsize(1080, 650)

            self.accounts: list[AccountFile] = []
            self.selected_path: Path | None = None
            self.is_new_account = False
            self.item_frames: dict[Path, tk.Frame] = {}

            self.name_var = tk.StringVar()
            self.title_edit_var = tk.StringVar()
            self.file_var = tk.StringVar()
            self.status_var = tk.StringVar()
            self.size_var = tk.StringVar()
            self.modified_var = tk.StringVar()
            self.refresh_var = tk.StringVar()
            self.usage_text_vars: dict[str, tk.StringVar] = {}
            self.usage_bar_canvases: dict[str, tk.Canvas] = {}
            self.usage_bar_values: dict[str, float] = {}
            self.digest_var = tk.StringVar()
            self.footer_var = tk.StringVar()
            self.json_buffer = ""
            self.detail_window: tk.Toplevel | None = None
            self.detail_text: tk.Text | None = None

            self.build_ui()
            self.refresh_accounts(select_active=True)
            self.center_window()

        def center_window(self) -> None:
            self.root.update_idletasks()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            x = (self.root.winfo_screenwidth() // 2) - (width // 2)
            y = (self.root.winfo_screenheight() // 2) - (height // 2)
            self.root.geometry(f"{width}x{height}+{x}+{y}")

        def build_ui(self) -> None:
            self.main = tk.Frame(self.root, bg=Theme.bg)
            self.main.pack(fill=tk.BOTH, expand=True)
            self.build_sidebar()
            self.build_main_panel()

        def build_sidebar(self) -> None:
            self.sidebar = tk.Frame(self.main, bg=Theme.sidebar, width=360)
            self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
            self.sidebar.pack_propagate(False)

            header = tk.Frame(self.sidebar, bg=Theme.sidebar, height=78)
            header.pack(fill=tk.X)
            header.pack_propagate(False)

            tk.Label(
                header,
                text="Codex Auth",
                bg=Theme.sidebar,
                fg=Theme.text,
                font=(Theme.font, 15, "bold"),
            ).pack(anchor=tk.W, padx=18, pady=(18, 2))
            tk.Label(
                header,
                text="账号文件切换器",
                bg=Theme.sidebar,
                fg=Theme.muted,
                font=(Theme.font, 9),
            ).pack(anchor=tk.W, padx=18)

            tk.Frame(self.sidebar, bg=Theme.border, height=1).pack(fill=tk.X)

            tools = tk.Frame(self.sidebar, bg=Theme.sidebar)
            tools.pack(fill=tk.X, padx=14, pady=14)
            self.make_text_button(tools, "+ 新建空白", self.new_account, secondary=True).pack(fill=tk.X)
            self.make_text_button(tools, "导入 JSON 文件", self.import_account, secondary=True).pack(fill=tk.X, pady=(8, 0))
            self.make_text_button(tools, "添加当前登录", self.add_current_auth, secondary=True).pack(fill=tk.X, pady=(8, 0))
            self.make_text_button(tools, "刷新全部用量", self.refresh_all_usage, secondary=True).pack(fill=tk.X, pady=(8, 0))

            list_header = tk.Frame(self.sidebar, bg=Theme.sidebar)
            list_header.pack(fill=tk.X, padx=18, pady=(4, 8))
            tk.Label(
                list_header,
                text="SAVED ACCOUNTS",
                bg=Theme.sidebar,
                fg=Theme.muted,
                font=(Theme.font, 9, "bold"),
            ).pack(side=tk.LEFT)
            refresh = tk.Label(
                list_header,
                text="刷新",
                bg=Theme.sidebar,
                fg=Theme.muted,
                font=(Theme.font, 9),
                cursor="hand2",
            )
            refresh.pack(side=tk.RIGHT)
            refresh.bind("<Button-1>", lambda _event: self.refresh_accounts())

            self.list_canvas = tk.Canvas(self.sidebar, bg=Theme.sidebar, highlightthickness=0)
            self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0), pady=(0, 12))
            scrollbar = ttk.Scrollbar(self.sidebar, orient=tk.VERTICAL, command=self.list_canvas.yview)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 12))
            self.list_canvas.configure(yscrollcommand=scrollbar.set)
            self.list_frame = tk.Frame(self.list_canvas, bg=Theme.sidebar)
            self.list_window = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
            self.list_frame.bind(
                "<Configure>",
                lambda _event: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")),
            )
            self.list_canvas.bind(
                "<Configure>",
                lambda event: self.list_canvas.itemconfigure(self.list_window, width=event.width),
            )

        def build_main_panel(self) -> None:
            content = tk.Frame(self.main, bg=Theme.bg)
            content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            topbar = tk.Frame(content, bg=Theme.bg, height=78)
            topbar.pack(fill=tk.X)
            topbar.pack_propagate(False)

            self.title_frame = tk.Frame(topbar, bg=Theme.bg)
            self.title_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=32, pady=18)

            self.title_label = tk.Label(
                self.title_frame,
                text="选择一个账号",
                bg=Theme.bg,
                fg=Theme.text,
                font=(Theme.font, 15, "bold"),
                cursor="hand2",
            )
            self.title_label.pack(side=tk.LEFT, pady=6)
            self.title_label.bind("<Button-1>", lambda _event: self.start_title_edit())
            self.title_label.bind("<Enter>", lambda _event: self.title_label.config(fg=Theme.subtext))
            self.title_label.bind("<Leave>", lambda _event: self.title_label.config(fg=Theme.text))

            self.title_entry = tk.Entry(
                self.title_frame,
                textvariable=self.title_edit_var,
                bg=Theme.bg,
                fg=Theme.text,
                insertbackground=Theme.text,
                font=(Theme.font, 14, "bold"),
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground="#cbd5e1",
                highlightcolor="#cbd5e1",
            )
            self.title_entry.bind("<Return>", lambda _event: self.save_title_name())
            self.title_entry.bind("<Escape>", lambda _event: self.show_title_display(self.title_label.cget("text")))
            self.title_save_button = self.make_save_icon_button(self.title_frame, self.save_title_name)

            top_actions = tk.Frame(topbar, bg=Theme.bg)
            top_actions.pack(side=tk.RIGHT, padx=32, pady=18)
            self.make_text_button(top_actions, "刷新用量", self.refresh_selected_usage, secondary=True).pack(side=tk.LEFT, padx=(0, 8))
            self.make_text_button(top_actions, "详情 / 编辑 JSON", self.open_details, secondary=True).pack(side=tk.LEFT, padx=(0, 8))
            self.make_text_button(top_actions, "更新此账号", self.update_selected_from_current, secondary=True).pack(side=tk.LEFT, padx=(0, 8))
            self.make_text_button(top_actions, "切换为此账号", self.apply_selected, primary=True).pack(side=tk.LEFT)

            self.active_label = tk.Label(
                topbar,
                text="",
                bg=Theme.bg,
                fg=Theme.success,
                font=(Theme.font, 10),
            )
            self.active_label.pack(side=tk.RIGHT, padx=(0, 16))

            tk.Frame(content, bg=Theme.border, height=1).pack(fill=tk.X)

            form = tk.Frame(content, bg=Theme.bg)
            form.pack(fill=tk.BOTH, expand=True, padx=32, pady=24)

            self.create_usage_panel(form)

            self.build_action_bar(content)

        def build_action_bar(self, parent: tk.Frame) -> None:
            bar = tk.Frame(parent, bg=Theme.bg, height=74)
            bar.pack(fill=tk.X, side=tk.BOTTOM)
            bar.pack_propagate(False)
            tk.Frame(bar, bg=Theme.border, height=1).pack(fill=tk.X, side=tk.TOP)

            inner = tk.Frame(bar, bg=Theme.bg)
            inner.pack(fill=tk.BOTH, expand=True, padx=32)

            self.footer_label = tk.Label(
                inner,
                textvariable=self.footer_var,
                bg=Theme.bg,
                fg=Theme.muted,
                font=(Theme.font, 9),
            )
            self.footer_label.pack(side=tk.LEFT, pady=22)

        def make_text_button(self, parent: tk.Frame, text: str, command, primary: bool = False, secondary: bool = False):
            bg = Theme.primary_bg if primary else Theme.panel_alt if secondary else Theme.bg
            fg = Theme.primary_fg if primary else Theme.subtext
            label = tk.Label(
                parent,
                text=text,
                bg=bg,
                fg=fg,
                font=(Theme.font, 10, "bold" if primary else "normal"),
                padx=16,
                pady=8,
                cursor="hand2",
            )
            label.bind("<Button-1>", lambda _event: command())
            label.bind("<Enter>", lambda _event: label.config(bg="#ffffff" if primary else Theme.hover))
            label.bind("<Leave>", lambda _event: label.config(bg=bg))
            return label

        def make_save_icon_button(self, parent: tk.Frame, command) -> tk.Canvas:
            canvas = tk.Canvas(parent, width=30, height=30, bg=Theme.panel_alt, highlightthickness=0, cursor="hand2")

            def draw(bg: str) -> None:
                canvas.config(bg=bg)
                canvas.delete("all")
                canvas.create_rectangle(6, 4, 24, 26, fill=bg, outline=Theme.border, width=1)
                canvas.create_rectangle(9, 7, 20, 13, fill=Theme.subtext, outline="")
                canvas.create_rectangle(10, 19, 20, 24, outline=Theme.subtext, width=1)

            draw(Theme.panel_alt)
            canvas.bind("<Button-1>", lambda _event: command())
            canvas.bind("<Enter>", lambda _event: draw(Theme.hover))
            canvas.bind("<Leave>", lambda _event: draw(Theme.panel_alt))
            return canvas

        def create_usage_panel(self, parent: tk.Frame) -> None:
            frame = tk.Frame(parent, bg=Theme.bg)
            frame.pack(fill=tk.X, pady=(0, 12))
            tk.Label(frame, text="剩余用量", bg=Theme.bg, fg=Theme.muted, font=(Theme.font, 9)).pack(anchor=tk.W, pady=(0, 6))

            cards = tk.Frame(frame, bg=Theme.bg)
            cards.pack(fill=tk.X)
            self.create_usage_card(cards, "5h", "5 小时窗口").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            self.create_usage_card(cards, "7d", "7 天窗口").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        def create_usage_card(self, parent: tk.Frame, key: str, title: str) -> tk.Frame:
            card = tk.Frame(parent, bg=Theme.panel, highlightthickness=1, highlightbackground=Theme.border)
            tk.Label(card, text=title, bg=Theme.panel, fg=Theme.dim, font=(Theme.font, 8)).pack(
                anchor=tk.W,
                padx=10,
                pady=(9, 2),
            )

            text_var = tk.StringVar(value="未刷新")
            self.usage_text_vars[key] = text_var
            tk.Label(
                card,
                textvariable=text_var,
                bg=Theme.panel,
                fg=Theme.subtext,
                font=(Theme.font, 9),
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=310,
            ).pack(fill=tk.X, padx=10, pady=(0, 8))

            canvas = tk.Canvas(card, height=10, bg=Theme.panel, highlightthickness=0)
            canvas.pack(fill=tk.X, padx=10, pady=(0, 10))
            self.usage_bar_canvases[key] = canvas
            self.usage_bar_values[key] = 0.0
            canvas.bind("<Configure>", lambda _event, bar_key=key: self.draw_usage_bar(bar_key))
            return card

        def show_title_display(self, text: str) -> None:
            self.title_entry.pack_forget()
            self.title_save_button.pack_forget()
            self.title_label.config(text=text or "未命名账号")
            if not self.title_label.winfo_ismapped():
                self.title_label.pack(side=tk.LEFT, pady=6)

        def start_title_edit(self) -> None:
            if not self.is_new_account and self.get_selected_account() is None:
                return
            self.title_edit_var.set(self.name_var.get())
            self.title_label.pack_forget()
            self.title_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, ipadx=10, pady=0)
            self.title_save_button.pack(side=tk.LEFT, padx=(8, 0), pady=4)
            self.title_entry.focus_set()
            self.title_entry.selection_range(0, tk.END)

        def save_title_name(self) -> None:
            previous = self.name_var.get()
            self.name_var.set(self.title_edit_var.get())
            if self.update_account_name():
                self.show_title_display(self.name_var.get())
            else:
                self.name_var.set(previous)

        def create_readonly_row(self, parent: tk.Frame, label_text: str, var: tk.StringVar, mono: bool = False) -> None:
            frame = tk.Frame(parent, bg=Theme.bg)
            frame.pack(fill=tk.X, pady=(0, 10))
            tk.Label(frame, text=label_text, bg=Theme.bg, fg=Theme.muted, font=(Theme.font, 9)).pack(anchor=tk.W, pady=(0, 6))
            tk.Label(
                frame,
                textvariable=var,
                bg=Theme.panel,
                fg=Theme.subtext,
                font=(Theme.mono if mono else Theme.font, 9),
                anchor=tk.W,
                justify=tk.LEFT,
                padx=10,
                pady=9,
                wraplength=680,
            ).pack(fill=tk.X)

        def create_meta_card(self, parent: tk.Frame, title: str, var: tk.StringVar) -> tk.Frame:
            card = tk.Frame(parent, bg=Theme.panel, highlightthickness=1, highlightbackground=Theme.border)
            tk.Label(card, text=title, bg=Theme.panel, fg=Theme.dim, font=(Theme.font, 8)).pack(anchor=tk.W, padx=10, pady=(8, 0))
            tk.Label(card, textvariable=var, bg=Theme.panel, fg=Theme.subtext, font=(Theme.font, 10), anchor=tk.W).pack(
                anchor=tk.W,
                padx=10,
                pady=(3, 9),
            )
            return card

        def open_details(self) -> None:
            if self.detail_window and self.detail_window.winfo_exists():
                self.detail_window.lift()
                self.detail_window.focus_force()
                return

            window = tk.Toplevel(self.root)
            self.detail_window = window
            window.title("账号详细信息 / 编辑 JSON")
            window.configure(bg=Theme.bg)
            window.geometry("860x680")
            window.minsize(720, 560)
            window.transient(self.root)

            def close_window() -> None:
                if self.detail_text and self.detail_text.winfo_exists():
                    self.json_buffer = self.detail_text.get("1.0", tk.END)
                self.detail_text = None
                self.detail_window = None
                window.destroy()

            window.protocol("WM_DELETE_WINDOW", close_window)

            header = tk.Frame(window, bg=Theme.bg)
            header.pack(fill=tk.X, padx=24, pady=(20, 12))
            tk.Label(
                header,
                text="详细信息 / 编辑 JSON",
                bg=Theme.bg,
                fg=Theme.text,
                font=(Theme.font, 15, "bold"),
            ).pack(side=tk.LEFT)

            actions = tk.Frame(header, bg=Theme.bg)
            actions.pack(side=tk.RIGHT)
            self.make_text_button(actions, "格式化 JSON", self.format_json, secondary=True).pack(side=tk.LEFT, padx=(0, 8))
            self.make_text_button(actions, "保存账号", self.save_account, secondary=True).pack(side=tk.LEFT, padx=(0, 8))
            self.make_text_button(actions, "关闭", close_window, primary=True).pack(side=tk.LEFT)

            body = tk.Frame(window, bg=Theme.bg)
            body.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 20))

            info = tk.Frame(body, bg=Theme.bg)
            info.pack(fill=tk.X)
            self.create_readonly_row(info, "来源文件", self.file_var)
            meta = tk.Frame(info, bg=Theme.bg)
            meta.pack(fill=tk.X, pady=(0, 10))
            self.create_meta_card(meta, "当前状态", self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            self.create_meta_card(meta, "大小", self.size_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
            self.create_meta_card(meta, "修改时间", self.modified_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
            self.create_readonly_row(info, "auth last_refresh", self.refresh_var, mono=True)
            self.create_readonly_row(info, "SHA256", self.digest_var, mono=True)

            tk.Label(
                body,
                text="auth.json 内容",
                bg=Theme.bg,
                fg=Theme.muted,
                font=(Theme.font, 9),
            ).pack(anchor=tk.W, pady=(4, 6))

            editor_frame = tk.Frame(body, bg=Theme.panel, highlightthickness=1, highlightbackground=Theme.border)
            editor_frame.pack(fill=tk.BOTH, expand=True)
            self.detail_text = tk.Text(
                editor_frame,
                bg=Theme.panel,
                fg=Theme.subtext,
                insertbackground=Theme.subtext,
                selectbackground=Theme.selected,
                font=(Theme.mono, 10),
                relief=tk.FLAT,
                undo=True,
                wrap=tk.NONE,
            )
            self.detail_text.insert("1.0", self.json_buffer)
            self.detail_text.edit_modified(False)

            yscroll = ttk.Scrollbar(editor_frame, orient=tk.VERTICAL, command=self.detail_text.yview)
            xscroll = ttk.Scrollbar(editor_frame, orient=tk.HORIZONTAL, command=self.detail_text.xview)
            self.detail_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            self.detail_text.grid(row=0, column=0, sticky="nsew")
            yscroll.grid(row=0, column=1, sticky="ns")
            xscroll.grid(row=1, column=0, sticky="ew")
            editor_frame.columnconfigure(0, weight=1)
            editor_frame.rowconfigure(0, weight=1)

            window.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (window.winfo_width() // 2)
            y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (window.winfo_height() // 2)
            window.geometry(f"{window.winfo_width()}x{window.winfo_height()}+{max(x, 0)}+{max(y, 0)}")

        def reset_usage_view(self, message: str) -> None:
            for key in ("5h", "7d"):
                self.usage_text_vars[key].set(message)
                self.usage_bar_values[key] = 0.0
                self.draw_usage_bar(key)

        def update_usage_view(self, usage: dict[str, Any] | None) -> None:
            if not usage:
                self.reset_usage_view("未刷新")
                return
            if not usage.get("success"):
                self.reset_usage_view(str(usage.get("error") or "查询失败"))
                return

            self.update_usage_tier("5h", usage, ("5h", "five_hour"))
            self.update_usage_tier("7d", usage, ("7d", "seven_day"))

        def update_usage_tier(self, key: str, usage: dict[str, Any], names: tuple[str, ...]) -> None:
            tier = self.find_usage_tier(usage, names)
            if not tier:
                self.usage_text_vars[key].set("没有返回此窗口")
                self.usage_bar_values[key] = 0.0
                self.draw_usage_bar(key)
                return

            used = float(tier.get("used_percent", 0.0))
            remaining = float(tier.get("remaining_percent", max(0.0, 100.0 - used)))
            reset_at = tier.get("reset_at") or "-"
            self.usage_text_vars[key].set(f"剩余 {remaining:.1f}%\n重置 {reset_at}")
            self.usage_bar_values[key] = max(0.0, min(100.0, remaining))
            self.draw_usage_bar(key)

        def find_usage_tier(self, usage: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any] | None:
            tiers = usage.get("tiers")
            if not isinstance(tiers, list):
                return None
            wanted = {name.casefold() for name in names}
            for tier in tiers:
                if isinstance(tier, dict) and str(tier.get("name", "")).casefold() in wanted:
                    return tier
            return None

        def draw_usage_bar(self, key: str) -> None:
            canvas = self.usage_bar_canvases.get(key)
            if not canvas:
                return
            remaining = self.usage_bar_values.get(key, 0.0)
            width = max(canvas.winfo_width(), 1)
            height = max(canvas.winfo_height(), 10)
            canvas.delete("all")
            canvas.create_rectangle(0, 0, width, height, fill=Theme.panel_alt, outline="")

            fill_width = int(width * remaining / 100.0)
            if remaining >= 50:
                fill = Theme.success
            elif remaining >= 20:
                fill = "#f59e0b"
            else:
                fill = Theme.danger
            canvas.create_rectangle(0, 0, fill_width, height, fill=fill, outline="")

        def refresh_accounts(self, select_active: bool = False) -> None:
            try:
                self.accounts = discover_accounts()
            except Exception as exc:
                messagebox.showerror("加载失败", str(exc))
                self.accounts = []

            active = active_account(self.accounts)
            if select_active and active:
                self.selected_path = active.path
            elif self.selected_path and not any(account.path == self.selected_path for account in self.accounts):
                self.selected_path = None
            elif not self.selected_path and self.accounts:
                self.selected_path = self.accounts[0].path

            self.render_account_list()
            selected = self.get_selected_account()
            if selected:
                self.load_account(selected)
            elif self.accounts:
                self.load_account(self.accounts[0])
            else:
                self.show_empty_state()

        def render_account_list(self) -> None:
            for widget in self.list_frame.winfo_children():
                widget.destroy()
            self.item_frames.clear()
            active = active_account(self.accounts)
            for account in self.accounts:
                self.create_account_item(account, active)

        def create_account_item(self, account: AccountFile, active: AccountFile | None) -> None:
            is_selected = self.selected_path == account.path
            is_active = bool(active and active.path == account.path)
            bg = Theme.selected if is_selected else Theme.sidebar
            fg = Theme.text if is_selected else Theme.subtext

            item = tk.Frame(self.list_frame, bg=bg, cursor="hand2")
            item.pack(fill=tk.X, pady=1)
            inner = tk.Frame(item, bg=bg)
            inner.pack(fill=tk.X, padx=12, pady=7)

            text_block = tk.Frame(inner, bg=bg)
            text_block.pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(
                text_block,
                text=account.display_label,
                bg=bg,
                fg=fg,
                font=(Theme.font, 9, "bold" if is_selected else "normal"),
                anchor=tk.W,
            ).pack(fill=tk.X, anchor=tk.W)
            preview = tk.Frame(text_block, bg=bg)
            preview.pack(fill=tk.X, pady=(6, 0))
            usage = get_cached_usage(account.path)
            self.create_sidebar_usage_row(preview, "5h", self.usage_remaining(usage, ("5h", "five_hour")), bg).pack(
                fill=tk.X,
                pady=(0, 2),
            )
            self.create_sidebar_usage_row(preview, "7d", self.usage_remaining(usage, ("7d", "seven_day")), bg).pack(
                fill=tk.X,
            )
            if is_active:
                tk.Label(inner, text="ACTIVE", bg=bg, fg=Theme.success, font=(Theme.font, 7, "bold")).pack(side=tk.RIGHT)

            def select(_event=None, path=account.path) -> None:
                self.selected_path = path
                self.is_new_account = False
                self.refresh_accounts()

            def enter(_event=None) -> None:
                if self.selected_path != account.path:
                    self.set_item_bg(item, Theme.hover)

            def leave(_event=None) -> None:
                if self.selected_path != account.path:
                    self.set_item_bg(item, Theme.sidebar)

            for widget in self.walk_widgets(item):
                widget.bind("<Button-1>", select)
                widget.bind("<Enter>", enter)
                widget.bind("<Leave>", leave)
            self.item_frames[account.path] = item

        def create_sidebar_usage_row(self, parent: tk.Frame, label: str, remaining: float | None, bg: str) -> tk.Frame:
            row = tk.Frame(parent, bg=bg)
            text = "--" if remaining is None else f"{remaining:.0f}%"
            tk.Label(
                row,
                text=f"{label} {text}",
                bg=bg,
                fg=Theme.dim,
                font=(Theme.font, 7),
                width=7,
                anchor=tk.W,
            ).pack(side=tk.LEFT)

            canvas = tk.Canvas(row, height=5, bg=bg, highlightthickness=0)
            canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0), pady=3)
            canvas.bind("<Configure>", lambda _event, widget=canvas, value=remaining: self.draw_sidebar_usage_bar(widget, value))
            return row

        def draw_sidebar_usage_bar(self, canvas: tk.Canvas, remaining: float | None) -> None:
            width = max(canvas.winfo_width(), 1)
            height = max(canvas.winfo_height(), 5)
            value = max(0.0, min(100.0, float(remaining or 0.0)))
            canvas.delete("all")
            canvas.create_rectangle(0, 0, width, height, fill=Theme.panel_alt, outline="")
            if value <= 0:
                return
            if value >= 50:
                fill = Theme.success
            elif value >= 20:
                fill = "#f59e0b"
            else:
                fill = Theme.danger
            canvas.create_rectangle(0, 0, int(width * value / 100.0), height, fill=fill, outline="")

        def usage_remaining(self, usage: dict[str, Any] | None, names: tuple[str, ...]) -> float | None:
            if not usage or not usage.get("success"):
                return None
            tier = self.find_usage_tier(usage, names)
            if not tier:
                return None
            used = float(tier.get("used_percent", 0.0))
            return float(tier.get("remaining_percent", max(0.0, 100.0 - used)))

        def walk_widgets(self, widget: tk.Widget):
            yield widget
            for child in widget.winfo_children():
                yield from self.walk_widgets(child)

        def set_item_bg(self, frame: tk.Frame, color: str) -> None:
            for widget in self.walk_widgets(frame):
                try:
                    widget.config(bg=color)
                except tk.TclError:
                    pass

        def get_selected_account(self) -> AccountFile | None:
            if self.selected_path is None:
                return None
            for account in self.accounts:
                if account.path == self.selected_path:
                    return account
            return None

        def load_account(self, account: AccountFile) -> None:
            self.selected_path = account.path
            self.is_new_account = False
            self.name_var.set(account.label)
            self.file_var.set(str(account.path))
            self.size_var.set(f"{account.size} bytes")
            self.modified_var.set(f"{account.modified:%Y-%m-%d %H:%M:%S}")
            self.refresh_var.set(auth_last_refresh(account.path))
            self.update_usage_view(get_cached_usage(account.path))
            self.digest_var.set(account.digest)

            active = active_account(self.accounts)
            if active and active.path == account.path:
                self.status_var.set("正在使用")
                self.active_label.config(text="Current Active")
            else:
                self.status_var.set("未启用")
                self.active_label.config(text="")

            self.show_title_display(account.display_label)
            try:
                formatted = validate_json_text(account.path.read_text(encoding="utf-8"))
            except Exception as exc:
                formatted = f"// Failed to load JSON: {exc}\n"
            self.set_editor_text(formatted)
            self.footer_var.set("保存会先校验 JSON；切换会先备份当前 auth.json。")

        def show_empty_state(self) -> None:
            self.selected_path = None
            self.name_var.set("")
            self.file_var.set("")
            self.status_var.set("无账号")
            self.size_var.set("-")
            self.modified_var.set("-")
            self.refresh_var.set("-")
            self.reset_usage_view("未刷新")
            self.digest_var.set("-")
            self.show_title_display("还没有账号")
            self.active_label.config(text="")
            self.set_editor_text("{\n  \n}\n")
            self.footer_var.set("点击左侧“添加账号”或“导入 JSON”开始。")

        def set_editor_text(self, value: str) -> None:
            self.json_buffer = value
            if self.detail_text and self.detail_text.winfo_exists():
                self.detail_text.delete("1.0", tk.END)
                self.detail_text.insert("1.0", value)
                self.detail_text.edit_modified(False)

        def editor_text(self) -> str:
            if self.detail_text and self.detail_text.winfo_exists():
                self.json_buffer = self.detail_text.get("1.0", tk.END)
            return self.json_buffer

        def new_account(self) -> None:
            self.selected_path = None
            self.is_new_account = True
            draft_name = f"account-{datetime.now():%Y%m%d-%H%M%S}"
            self.name_var.set(draft_name)
            self.file_var.set(str(account_path_for_label(draft_name)))
            self.status_var.set("新账号")
            self.size_var.set("-")
            self.modified_var.set("-")
            self.refresh_var.set("-")
            self.reset_usage_view("新账号保存后可刷新")
            self.digest_var.set("-")
            self.show_title_display(draft_name)
            self.active_label.config(text="")
            self.set_editor_text("{\n  \n}\n")
            self.render_account_list()
            self.footer_var.set("点击顶部名称可改名；在详情窗口粘贴 JSON 后保存。")
            self.open_details()

        def import_account(self) -> None:
            source = filedialog.askopenfilename(
                parent=self.root,
                title="导入账号 JSON",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not source:
                return
            source_path = Path(source)
            try:
                formatted = validate_json_text(source_path.read_text(encoding="utf-8"))
            except Exception as exc:
                messagebox.showerror("导入失败", str(exc))
                return
            self.new_account()
            self.name_var.set(label_for(source_path))
            self.file_var.set(str(account_path_for_label(self.name_var.get())))
            self.show_title_display(self.name_var.get())
            self.set_editor_text(formatted)
            self.footer_var.set("导入成功。检查名称和 JSON 后点“保存账号”。")
            self.open_details()

        def update_account_name(self) -> bool:
            try:
                clean_name = clean_account_label(self.name_var.get())
            except Exception as exc:
                messagebox.showerror("名称无效", str(exc))
                return False
            self.name_var.set(clean_name)

            if self.is_new_account or self.get_selected_account() is None:
                self.file_var.set(str(account_path_for_label(clean_name)))
                self.footer_var.set("账号名称已更新；新账号仍需要在详情窗口保存 JSON。")
                return True

            selected = self.get_selected_account()
            if selected is None:
                return False
            if clean_name == selected.label:
                self.footer_var.set("账号名称没有变化。")
                return True

            target = selected.path.with_name(f"{ACCOUNT_PREFIX}{clean_name}.json")
            if target.exists():
                messagebox.showerror("更新名称失败", f"目标文件已存在：{target.name}")
                self.name_var.set(selected.label)
                return False

            try:
                selected.path.rename(target)
                move_account_meta(selected.path, target)
            except Exception as exc:
                messagebox.showerror("更新名称失败", str(exc))
                self.name_var.set(selected.label)
                return False

            self.selected_path = target
            self.refresh_accounts()
            self.footer_var.set(f"账号名称已更新：{target.name}")
            return True

        def default_current_auth_label(self) -> str:
            active = active_account(self.accounts)
            if active:
                return active.label
            return f"account-{datetime.now():%Y%m%d-%H%M%S}"

        def add_current_auth(self) -> None:
            if not TARGET_AUTH.exists():
                messagebox.showwarning("找不到当前登录", f"没有找到：{TARGET_AUTH}")
                return
            try:
                validate_json_file(TARGET_AUTH)
            except Exception as exc:
                messagebox.showerror("当前 auth.json 无效", str(exc))
                return

            active = active_account(self.accounts)
            if active and not messagebox.askyesno(
                "当前登录已存在",
                f"当前 auth.json 已经匹配账号“{active.display_label}”。\n\n仍然要另存一份吗？",
            ):
                self.selected_path = active.path
                self.refresh_accounts()
                return

            label = simpledialog.askstring(
                "添加当前登录",
                "给当前登录账号起一个名字：",
                initialvalue=self.default_current_auth_label(),
                parent=self.root,
            )
            if label is None:
                return
            try:
                target = account_path_for_label(label)
            except Exception as exc:
                messagebox.showerror("账号名称无效", str(exc))
                return
            if target.exists() and not messagebox.askyesno("覆盖账号", f"{target.name} 已存在，是否覆盖？"):
                return

            try:
                copy_current_auth_to(target)
            except Exception as exc:
                messagebox.showerror("添加失败", str(exc))
                return

            self.selected_path = target
            self.is_new_account = False
            self.refresh_accounts()
            self.footer_var.set(f"已从当前 Codex 登录添加：{target.name}")

        def update_selected_from_current(self) -> None:
            selected = self.get_selected_account()
            if selected is None:
                messagebox.showwarning("未选择账号", "请先在左侧选择要更新的账号。")
                return
            if not TARGET_AUTH.exists():
                messagebox.showwarning("找不到当前登录", f"没有找到：{TARGET_AUTH}")
                return
            if not messagebox.askyesno(
                "更新账号",
                f"将用当前 Codex 登录覆盖：{selected.path.name}\n\n旧文件会先备份到 backups。继续吗？",
            ):
                return
            try:
                copy_current_auth_to(selected.path)
            except Exception as exc:
                messagebox.showerror("更新失败", str(exc))
                return
            self.selected_path = selected.path
            self.refresh_accounts()
            self.footer_var.set(f"已根据当前 Codex 登录更新：{selected.path.name}")

        def refresh_selected_usage(self) -> None:
            selected = self.get_selected_account()
            if selected is None:
                messagebox.showwarning("未选择账号", "请先在左侧选择要刷新用量的账号。")
                return
            self.footer_var.set("正在查询用量...")
            self.root.update_idletasks()
            try:
                usage = query_codex_usage(selected.path)
                set_cached_usage(selected.path, usage)
            except Exception as exc:
                usage = {
                    "success": False,
                    "error": str(exc),
                    "queried_at": datetime.now().isoformat(timespec="seconds"),
                }
                set_cached_usage(selected.path, usage)
                self.update_usage_view(usage)
                self.footer_var.set("用量查询失败。")
                messagebox.showerror("用量查询失败", str(exc))
                return

            self.update_usage_view(usage)
            self.render_account_list()
            self.footer_var.set(f"用量已刷新：{selected.display_label}")

        def refresh_all_usage(self) -> None:
            if not self.accounts:
                messagebox.showwarning("没有账号", "请先添加账号。")
                return

            errors: list[str] = []
            for index, account in enumerate(self.accounts, start=1):
                self.footer_var.set(f"正在查询用量 {index}/{len(self.accounts)}：{account.display_label}")
                self.root.update_idletasks()
                try:
                    usage = query_codex_usage(account.path)
                except Exception as exc:
                    usage = {
                        "success": False,
                        "error": str(exc),
                        "queried_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    errors.append(f"{account.display_label}: {exc}")
                set_cached_usage(account.path, usage)

            self.render_account_list()
            selected = self.get_selected_account()
            if selected:
                self.update_usage_view(get_cached_usage(selected.path))
            if errors:
                self.footer_var.set(f"已刷新完成，{len(errors)} 个账号失败。")
                messagebox.showwarning("部分账号查询失败", "\n".join(errors[:5]))
            else:
                self.footer_var.set("全部账号用量已刷新。")

        def format_json(self) -> None:
            try:
                formatted = validate_json_text(self.editor_text())
            except Exception as exc:
                messagebox.showerror("JSON 无效", str(exc))
                return
            self.set_editor_text(formatted)
            self.footer_var.set("JSON 已格式化。")

        def save_account(self) -> None:
            try:
                formatted = validate_json_text(self.editor_text())
                clean_name = clean_account_label(self.name_var.get())
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))
                return

            selected = self.get_selected_account()
            if self.is_new_account or selected is None:
                target = account_path_for_label(clean_name)
                if target.exists() and not messagebox.askyesno("覆盖账号", f"{target.name} 已存在，是否覆盖？"):
                    return
            else:
                target = selected.path
                if clean_name != selected.label:
                    renamed = selected.path.with_name(f"{ACCOUNT_PREFIX}{clean_name}.json")
                    if renamed.exists() and renamed != selected.path:
                        messagebox.showerror("保存失败", f"目标文件已存在：{renamed.name}")
                        return
                    target = renamed

            try:
                if target.exists():
                    make_account_backup(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(formatted, encoding="utf-8")
                if selected and target != selected.path:
                    move_account_meta(selected.path, target)
                    make_account_backup(selected.path)
                    selected.path.unlink()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))
                return

            self.set_editor_text(formatted)
            self.selected_path = target
            self.is_new_account = False
            self.refresh_accounts()
            self.footer_var.set(f"已保存：{target.name}")

        def apply_selected(self) -> None:
            selected = self.get_selected_account()
            if selected is None:
                if self.is_new_account:
                    messagebox.showwarning("先保存账号", "新账号需要先保存，再切换。")
                else:
                    messagebox.showwarning("未选择账号", "请先选择一个账号。")
                return
            try:
                backup_path = switch_to(selected)
            except Exception as exc:
                messagebox.showerror("切换失败", str(exc))
                return
            self.refresh_accounts()
            if backup_path:
                self.footer_var.set(f"已切换到 {selected.display_label}；旧 auth.json 已备份。")
            else:
                self.footer_var.set(f"已切换到 {selected.display_label}。")

    if sys.platform == "win32":
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    AccountSwitcherApp(root)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        return run_cli(argv)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
