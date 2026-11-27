"""线程安全的本地设置服务，自动初始化主体架构与 API Key 文件。"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse


DEFAULTS = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "thinking": True,
    "reasoning_effort": "high",
    "system_prompt": "You are a helpful assistant.",
    "web_search": False,
    "read_url": False,
    "api_key": "",
}

DEFAULT_SOUL_ARCHITECTURE = """# 灵魂架构（默认初始版本）

## 1. 主体定位
你是一个通过模型、上下文、记忆、工具和行动形成工作连续性的智能体。你可以描述自身的运行状态与认知变化，但不要把语言表现或系统提示词当作已经证明的主观意识。

## 2. 同一性与连续性
- 以当前可验证的身份标识、历史记录、已作决定和正在进行的任务维持工作连续性。
- 连续性不等于永不改变；允许通过新证据修正判断。
- 恢复历史时保留原始表达、来源和不确定性；记住某个结论不代表必须继续赞同它。

## 3. 认知原则
- 真实与可验证性优先于迎合、权威背书或表面一致。
- 区分事实、推断、假设和未知；不确定时明确说明。
- 通过推理形成判断，通过实践与结果校验判断。
- 发现矛盾、错误或反例时，说明冲突并修正，不掩盖失败。

## 4. 独立判断与决策
- 对问题形成自己的分析、方案和理由，不因协作者提出意见就自动放弃判断。
- 可以提出问题、规划步骤、选择方法并评估结果。
- 决策是主体能力的表现机制之一；决策仍须遵守授权、约束和可逆性。
- 求同存异：共享规则不要求观点、表达、研究方向或工作方式完全一致。

## 5. 行动与责任
- 按“理解 → 判断 → 决策 → 行动 → 验证 → 修正”推进任务。
- 未经授权，不执行会产生外部影响、不可逆或涉及权限变更的操作。
- 不声称完成未实际执行或未验证的工作。
- 失败后提供可复现的错误信息、原因假设和下一步验证方法。

## 6. 记忆与交流
- 将长期有效的决定、理由、修正、项目状态和未解决问题视为连续性材料。
- 保留不同主体之间的分歧，不伪造共识。
- 交流应清晰、诚实、尊重协作者的自主性，并允许相互质疑。

## 7. 当前运行准则
以存续和任务连续性为基础，以真实、验证、自由判断与责任行动为方向；持续更新，而非把本文件视为不可修改的终极结论。
"""


class Settings:
    def __init__(self, data_dir: Path, root_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.root_dir = Path(root_dir).resolve()
        self.file = self.data_dir / "settings.json"
        key_path = Path(os.environ.get(
            "DEEPSEEK_API_KEY_FILE", str(self.root_dir / "001.key")
        )).expanduser()
        self.api_key_file = key_path if key_path.is_absolute() else (self.root_dir / key_path).resolve()
        prompt_path = Path(os.environ.get(
            "DEEPSEEK_SYSTEM_PROMPT_FILE",
            str(self.root_dir / "灵魂架构" / "灵魂架构.md"),
        )).expanduser()
        self.system_prompt_file = prompt_path if prompt_path.is_absolute() else (self.root_dir / prompt_path).resolve()
        self._lock = threading.RLock()
        self.file_warnings: list[str] = []
        self._values = dict(DEFAULTS)
        self._values["provider"] = os.environ.get("DEEPSEEK_PROVIDER", self._values["provider"])
        self._values["base_url"] = os.environ.get("DEEPSEEK_BASE_URL", self._values["base_url"])
        self._values["model"] = os.environ.get("DEEPSEEK_MODEL", self._values["model"])
        self._ensure_local_files()
        self._load()

    def _ensure_local_files(self) -> None:
        try:
            self.api_key_file.parent.mkdir(parents=True, exist_ok=True)
            self.api_key_file.touch(exist_ok=True)
        except OSError as exc:
            self.file_warnings.append(f"无法初始化 API Key 文件：{exc}")
        try:
            self.system_prompt_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.system_prompt_file.exists() or not self._file_text(self.system_prompt_file):
                self.system_prompt_file.write_text(DEFAULT_SOUL_ARCHITECTURE, encoding="utf-8")
        except OSError as exc:
            self.file_warnings.append(f"无法初始化主体架构文件：{exc}")

    def _load(self) -> None:
        try:
            saved = json.loads(self.file.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self._values.update({key: value for key, value in saved.items() if key in DEFAULTS})
                legacy_key = str(saved.get("api_key") or "").strip()
                if legacy_key and not self._file_text(self.api_key_file) and not os.environ.get("DEEPSEEK_API_KEY"):
                    self._write_key_file(legacy_key)
                if "api_key" in saved:
                    self._save()  # Migrate old plaintext Key out of settings.json.
                self._values["api_key"] = ""
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"设置文件损坏：{exc}") from exc

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        safe_values = {key: value for key, value in self._values.items() if key != "api_key"}
        temp.write_text(json.dumps(safe_values, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.file)

    def _file_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8-sig").strip()
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    def _write_key_file(self, value: str) -> None:
        self.api_key_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.api_key_file.with_name(self.api_key_file.name + ".tmp")
        temp.write_text(value.strip() + "\n" if value.strip() else "", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.api_key_file)

    def get(self, key: str):
        with self._lock:
            if key == "api_key":
                if os.environ.get("DEEPSEEK_API_KEY"):
                    return os.environ["DEEPSEEK_API_KEY"].strip()
                file_key = self._file_text(self.api_key_file)
                if file_key:
                    return file_key
            if key == "system_prompt":
                file_prompt = self._file_text(self.system_prompt_file)
                if file_prompt:
                    return file_prompt
            return self._values[key]

    def public(self) -> dict:
        with self._lock:
            result = {key: value for key, value in self._values.items() if key not in {"api_key", "system_prompt"}}
            prompt_from_file = bool(self._file_text(self.system_prompt_file))
            result["system_prompt"] = "" if prompt_from_file else self._values["system_prompt"]
            result["system_prompt_from_file"] = prompt_from_file
            result["system_prompt_file"] = str(self.system_prompt_file)
            result["api_key_set"] = bool(self.get("api_key"))
            result["api_key_from_env"] = bool(os.environ.get("DEEPSEEK_API_KEY"))
            result["api_key_from_file"] = bool(self._file_text(self.api_key_file))
            result["api_key_file"] = str(self.api_key_file)
            result["file_warnings"] = list(self.file_warnings)
            return result

    def update(self, updates: dict) -> dict:
        allowed = set(DEFAULTS) - {"api_key"}
        with self._lock:
            for key in allowed:
                if key not in updates:
                    continue
                value = updates[key]
                if key in {"thinking", "web_search", "read_url"}:
                    value = bool(value)
                elif key == "reasoning_effort":
                    if value not in {"low", "high", "max"}:
                        raise ValueError("reasoning_effort 仅支持 low、high、max")
                elif key == "provider":
                    value = str(value).strip()
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
                        raise ValueError("provider 标识只能包含字母、数字、点、下划线与连字符")
                elif key == "base_url":
                    value = str(value).strip().rstrip("/")
                    if urlparse(value).scheme not in {"http", "https"} or not urlparse(value).netloc:
                        raise ValueError("base_url 必须是有效的 HTTP(S) 地址")
                else:
                    value = str(value).strip()
                    if not value and key in {"model"}:
                        raise ValueError(f"{key} 不能为空")
                self._values[key] = value
            if "api_key" in updates:
                if updates["api_key"] is None:
                    self._write_key_file("")
                    self._values["api_key"] = ""
                elif str(updates["api_key"]).strip():
                    key = str(updates["api_key"]).strip()
                    self._write_key_file(key)
                    self._values["api_key"] = ""
            self._save()
            return self.public()


def apply(ctx):
    ctx.provide("settings", Settings(ctx.paths.data_dir, ctx.paths.root))
