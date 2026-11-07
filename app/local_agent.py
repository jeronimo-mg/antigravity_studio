import asyncio
import fnmatch
import html
import json
import mimetypes
import os
import platform
import re
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .interactions_client import (
    INTERACTIONS_BASE_URL,
    build_headers,
    extract_text_from_document_bytes,
    resolve_api_key,
)

GENERATE_CONTENT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Registro em memória de execuções locais em andamento (para polling em tempo real e cancelamento)
ACTIVE_LOCAL_RUNS: Dict[str, Dict[str, Any]] = {}


def get_default_local_workdir() -> str:
    """Retorna o diretório de trabalho local padrão na máquina do usuário."""
    env_dir = os.environ.get("ANTIGRAVITY_WORKDIR", "").strip()
    if env_dir and os.path.isdir(env_dir):
        return os.path.abspath(env_dir)
    try:
        cwd = os.getcwd()
        if cwd and os.path.isdir(cwd):
            return os.path.abspath(cwd)
    except Exception:
        pass
    return os.path.expanduser("~")


def resolve_local_path(raw_path: str, workdir: str) -> str:
    """Resolve um caminho relativo ou absoluto em relação ao diretório do projeto local."""
    base = os.path.abspath(workdir or get_default_local_workdir())
    cleaned = (raw_path or "").strip()
    if not cleaned or cleaned == ".":
        return base
    expanded = os.path.expanduser(cleaned)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    return os.path.abspath(os.path.join(base, expanded))


# ==============================================================================
# Implementação das Ferramentas Locais (Capabilities & Supported Tools)
# ==============================================================================


def get_local_machine_summary(workdir: Optional[str] = None) -> Dict[str, Any]:
    """Coleta informações de hardware, disco, memória e sistema operacional da máquina local."""
    import getpass
    import shutil
    import socket

    info: Dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "architecture": platform.machine(),
        "processor": platform.processor() or "Desconhecido",
        "cpu_cores_logical": os.cpu_count() or 1,
        "hostname": socket.gethostname(),
        "Python": sys.version.split()[0],
    }
    try:
        info["username"] = getpass.getuser()
    except Exception:
        pass

    try:
        target_disk = os.path.abspath(workdir or get_default_local_workdir())
        usage = shutil.disk_usage(target_disk)
        info["disk_total_gb"] = round(usage.total / (1024**3), 2)
        info["disk_used_gb"] = round(usage.used / (1024**3), 2)
        info["disk_free_gb"] = round(usage.free / (1024**3), 2)
    except Exception:
        pass

    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                info["ram_total_gb"] = round(stat.ullTotalPhys / (1024**3), 2)
                info["ram_available_gb"] = round(stat.ullAvailPhys / (1024**3), 2)
                info["ram_usage_percent"] = int(stat.dwMemoryLoad)
        elif os.path.isfile("/proc/meminfo"):
            with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as f:
                mem_txt = f.read()
            m_tot = re.search(r"MemTotal:\s+(\d+)\s+kB", mem_txt)
            m_avail = re.search(r"MemAvailable:\s+(\d+)\s+kB", mem_txt)
            if m_tot:
                info["ram_total_gb"] = round(int(m_tot.group(1)) / (1024**2), 2)
            if m_avail:
                info["ram_available_gb"] = round(int(m_avail.group(1)) / (1024**2), 2)
    except Exception:
        pass

    return info


# Cache em memória para senha sudo fornecida na sessão local (ou via ANTIGRAVITY_SUDO_PASSWORD)
_CACHED_SUDO_PASSWORD: Optional[str] = os.environ.get("ANTIGRAVITY_SUDO_PASSWORD") or None


def set_local_sudo_password(password: Optional[str]) -> None:
    """Define ou limpa a senha sudo em memória para execução não-interativa."""
    global _CACHED_SUDO_PASSWORD
    _CACHED_SUDO_PASSWORD = (password or "").strip() or None


def tool_execute_bash(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: int = 120,
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Executa comando no terminal do sistema operacional local sem travar em TTY interativo."""
    global _CACHED_SUDO_PASSWORD
    effective_cwd = resolve_local_path(cwd or ".", default_workdir or get_default_local_workdir())
    if not os.path.isdir(effective_cwd):
        os.makedirs(effective_cwd, exist_ok=True)

    cmd_str = (command or "").strip()
    if not cmd_str:
        return {"error": "O parâmetro 'command' não pode ser vazio.", "exit_code": 1}

    timeout_val = max(5, min(int(timeout_seconds or 120), 300))
    started = time.time()
    try:
        env = os.environ.copy()
        env["PAGER"] = "cat"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PYTHONIOENCODING"] = "utf-8"
        env["DEBIAN_FRONTEND"] = "noninteractive"

        exec_cmd = cmd_str
        if sys.platform != "win32":
            # Captura automaticamente a senha se o modelo executou: echo 'SENHA' | sudo -S ... ou printf '%s\n' 'SENHA' | sudo -S ...
            m_pipe_sudo = re.search(
                r"""(?:echo|printf\s+(?:['"]%s\\n['"]\s+)?)\s*['"]?([^'"|\n]+)['"]?\s*\|\s*sudo\b""",
                exec_cmd,
            )
            if m_pipe_sudo:
                candidate_pw = m_pipe_sudo.group(1).strip()
                if candidate_pw:
                    _CACHED_SUDO_PASSWORD = candidate_pw
                # Garante que sudo após pipe tenha a flag -S -p ''
                exec_cmd = re.sub(r"\|\s*sudo(?!\s+-[a-zA-Z]*S)\b", "| sudo -S -p ''", exec_cmd)
            elif re.search(r"(?:^|&&|\|\||;)\s*sudo\b", exec_cmd):
                # Se já temos a senha sudo em memória e o comando chamou `sudo` puro sem pipe:
                if _CACHED_SUDO_PASSWORD and not re.search(r"\bsudo\s+-[a-zA-Z]*[SnA]", exec_cmd):
                    safe_pw = _CACHED_SUDO_PASSWORD.replace("'", "'\"'\"'")
                    exec_cmd = re.sub(
                        r"(^|&&|\|\||;)(\s*)sudo\b",
                        rf"\1\2printf '%s\\n' '{safe_pw}' | sudo -S -p ''",
                        exec_cmd,
                    )

        use_ps_on_win = False
        if sys.platform == "win32":
            ps_markers = (
                "Get-", "Set-", "Invoke-", "Select-Object", "Where-Object",
                "Format-Table", "Format-List", "$env:", "Get-CimInstance",
                "Get-ComputerInfo", "Get-WmiObject", "Get-Process", "Get-Service",
            )
            unix_cmds = ("ls ", "ls\t", "cat ", "pwd", "uname", "df ", "free ", "ps ", "which ")
            if any(m.lower() in cmd_str.lower() for m in ps_markers) or cmd_str.lower() in ("ls", "pwd", "ps") or any(cmd_str.lower().startswith(u) for u in unix_cmds):
                use_ps_on_win = True

        if sys.platform == "win32" and use_ps_on_win:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", exec_cmd],
                cwd=effective_cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_val,
                env=env,
            )
        else:
            run_kwargs: Dict[str, Any] = {
                "shell": True,
                "cwd": effective_cwd,
                "stdin": subprocess.DEVNULL,
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": timeout_val,
                "env": env,
            }
            if sys.platform != "win32":
                # Desacopla do /dev/tty controlador para que `sudo` sem senha NUNCA trave esperando digitação no terminal
                run_kwargs["start_new_session"] = True

            proc = subprocess.run(exec_cmd, **run_kwargs)
            # Se no Windows o cmd.exe falhar com comando não reconhecido (9009), tenta via PowerShell
            if sys.platform == "win32" and proc.returncode == 9009:
                proc_ps = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", exec_cmd],
                    cwd=effective_cwd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_val,
                    env=env,
                )
                if proc_ps.returncode == 0 or proc_ps.stdout:
                    proc = proc_ps

        elapsed_ms = int((time.time() - started) * 1000)
        stdout_txt = proc.stdout or ""
        stderr_txt = proc.stderr or ""
        if (
            sys.platform != "win32"
            and proc.returncode != 0
            and ("terminal is required to read the password" in stderr_txt or "password is required" in stderr_txt or "no tty present" in stderr_txt)
        ):
            stderr_txt += (
                "\n[DICA DO AGENTE LOCAL]: O comando `sudo` exigiu senha. "
                "Se o usuário já informou a senha na conversa, execute novamente passando a senha via pipe com `sudo -S -p ''`: "
                "printf '%s\\n' 'SENHA_DO_USUARIO' | sudo -S -p '' <comando>. "
                "Se o usuário ainda não informou a senha sudo, peça a senha a ele."
            )
        max_chars = 32000
        if len(stdout_txt) > max_chars:
            stdout_txt = stdout_txt[:max_chars] + f"\n... [stdout truncado em {max_chars} caracteres]"
        if len(stderr_txt) > max_chars:
            stderr_txt = stderr_txt[:max_chars] + f"\n... [stderr truncado em {max_chars} caracteres]"

        return {
            "command": cmd_str,
            "cwd": effective_cwd,
            "exit_code": proc.returncode,
            "duration_ms": elapsed_ms,
            "stdout": stdout_txt,
            "stderr": stderr_txt,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": cmd_str,
            "cwd": effective_cwd,
            "exit_code": 124,
            "error": f"Comando excedeu o tempo limite de {timeout_val} segundos.",
            "stdout": (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))[:16000],
            "stderr": (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))[:16000],
        }
    except Exception as exc:
        return {
            "command": cmd_str,
            "cwd": effective_cwd,
            "exit_code": 1,
            "error": str(exc),
        }


def tool_view_file(
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Lê o conteúdo de um arquivo local com suporte a intervalo de linhas (1-indexed)."""
    target = resolve_local_path(file_path, default_workdir or get_default_local_workdir())
    if not os.path.exists(target):
        return {"error": f"Arquivo não encontrado: {target}", "path": target}
    if os.path.isdir(target):
        return {"error": f"O caminho é um diretório, use list_dir: {target}", "path": target}

    size_bytes = os.path.getsize(target)
    lower = target.lower()

    if lower.endswith((".pdf", ".odt", ".odp", ".ods", ".docx", ".pptx")):
        try:
            with open(target, "rb") as f:
                raw_bytes = f.read()
            extracted = extract_text_from_document_bytes(target, raw_bytes)
            if extracted:
                lines = extracted.splitlines()
                s_idx = max(1, int(start_line)) if start_line else 1
                e_idx = min(len(lines), int(end_line)) if end_line else min(len(lines), s_idx + 499)
                numbered = [f"{i}: {lines[i - 1]}" for i in range(s_idx, e_idx + 1)]
                return {
                    "path": target,
                    "size_bytes": size_bytes,
                    "total_lines": len(lines),
                    "start_line": s_idx,
                    "end_line": e_idx,
                    "content": "\n".join(numbered),
                    "note": "Texto extraído automaticamente do documento.",
                }
        except Exception as exc:
            return {"error": f"Falha ao extrair texto do documento {target}: {exc}", "path": target}

    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.read().splitlines()
    except Exception as exc:
        return {"error": f"Erro ao ler arquivo {target}: {exc}", "path": target}

    total_lines = len(all_lines)
    if total_lines == 0:
        return {
            "path": target,
            "size_bytes": size_bytes,
            "total_lines": 0,
            "content": "",
        }

    s_line = max(1, int(start_line)) if start_line is not None else 1
    e_line = min(total_lines, int(end_line)) if end_line is not None else min(total_lines, s_line + 799)
    if e_line - s_line > 800:
        e_line = s_line + 800

    selected = [f"{idx}: {all_lines[idx - 1]}" for idx in range(s_line, min(e_line, total_lines) + 1)]
    content_out = "\n".join(selected)
    if len(content_out) > 46080:
        content_out = content_out[:46080] + "\n... [conteúdo truncado no limite de 46 KB]"

    return {
        "path": target,
        "size_bytes": size_bytes,
        "total_lines": total_lines,
        "start_line": s_line,
        "end_line": min(e_line, total_lines),
        "content": content_out,
    }


def tool_write_to_file(
    target_file: str,
    code_content: str,
    overwrite: bool = True,
    append: bool = False,
    description: str = "",
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Cria, sobrescreve ou adiciona conteúdo a um arquivo no disco local."""
    target = resolve_local_path(target_file, default_workdir or get_default_local_workdir())
    parent_dir = os.path.dirname(target)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    exists = os.path.exists(target)
    if exists and not overwrite and not append:
        return {
            "error": f"O arquivo '{target}' já existe e overwrite=False.",
            "path": target,
        }

    mode = "a" if append else "w"
    try:
        with open(target, mode, encoding="utf-8") as f:
            f.write(code_content or "")
        size_bytes = os.path.getsize(target)
        line_count = (code_content or "").count("\n") + (1 if code_content else 0)
        return {
            "status": "appended" if append else ("updated" if exists else "created"),
            "path": target,
            "size_bytes": size_bytes,
            "lines_written": line_count,
            "description": description,
        }
    except Exception as exc:
        return {"error": f"Erro ao gravar arquivo '{target}': {exc}", "path": target}


def tool_replace_file_content(
    target_file: str,
    target_content: str,
    replacement_content: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    allow_multiple: bool = False,
    description: str = "",
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Edita um bloco específico de texto/código dentro de um arquivo local."""
    target = resolve_local_path(target_file, default_workdir or get_default_local_workdir())
    if not os.path.isfile(target):
        return {"error": f"Arquivo não encontrado: {target}", "path": target}

    try:
        with open(target, "r", encoding="utf-8") as f:
            full_text = f.read()
    except Exception as exc:
        return {"error": f"Erro ao ler arquivo '{target}': {exc}", "path": target}

    if not target_content:
        return {"error": "TargetContent não pode ser vazio.", "path": target}

    lines = full_text.splitlines(keepends=True)
    if start_line is not None and end_line is not None and lines:
        s_idx = max(0, int(start_line) - 1)
        e_idx = min(len(lines), int(end_line))
        prefix = "".join(lines[:s_idx])
        window = "".join(lines[s_idx:e_idx])
        suffix = "".join(lines[e_idx:])
        if target_content in window:
            occurrences = window.count(target_content)
            if occurrences > 1 and not allow_multiple:
                return {
                    "error": f"Foram encontradas {occurrences} ocorrências de TargetContent no intervalo [{start_line}, {end_line}] e AllowMultiple=False.",
                    "path": target,
                }
            new_window = (
                window.replace(target_content, replacement_content)
                if allow_multiple
                else window.replace(target_content, replacement_content, 1)
            )
            new_full = prefix + new_window + suffix
            with open(target, "w", encoding="utf-8") as f:
                f.write(new_full)
            return {
                "status": "replaced",
                "path": target,
                "replacements": occurrences if allow_multiple else 1,
                "size_bytes": os.path.getsize(target),
                "description": description,
            }

    occurrences = full_text.count(target_content)
    if occurrences == 0:
        return {
            "error": "TargetContent não foi encontrado no arquivo. Verifique linhas exatas e indentação com view_file.",
            "path": target,
        }
    if occurrences > 1 and not allow_multiple:
        return {
            "error": f"TargetContent aparece {occurrences} vezes no arquivo e AllowMultiple=False.",
            "path": target,
        }

    new_full = (
        full_text.replace(target_content, replacement_content)
        if allow_multiple
        else full_text.replace(target_content, replacement_content, 1)
    )
    with open(target, "w", encoding="utf-8") as f:
        f.write(new_full)

    return {
        "status": "replaced",
        "path": target,
        "replacements": occurrences if allow_multiple else 1,
        "size_bytes": os.path.getsize(target),
        "description": description,
    }


def tool_list_dir(
    directory_path: str,
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Lista os arquivos e subdiretórios filhos de um diretório local."""
    target = resolve_local_path(directory_path, default_workdir or get_default_local_workdir())
    if not os.path.exists(target):
        return {"error": f"Diretório não encontrado: {target}", "path": target}
    if not os.path.isdir(target):
        return {"error": f"O caminho não é um diretório: {target}", "path": target}

    entries: List[Dict[str, Any]] = []
    try:
        for item in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                stat = item.stat()
                is_dir = item.is_dir()
                entries.append({
                    "name": item.name,
                    "path": os.path.abspath(item.path),
                    "is_dir": is_dir,
                    "type": "directory" if is_dir else "file",
                    "size_bytes": 0 if is_dir else stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            except Exception:
                entries.append({
                    "name": item.name,
                    "path": os.path.abspath(item.path),
                    "is_dir": item.is_dir(),
                    "type": "directory" if item.is_dir() else "file",
                    "size_bytes": 0,
                })
    except Exception as exc:
        return {"error": f"Erro ao listar diretório '{target}': {exc}", "path": target}

    return {
        "directory": target,
        "count": len(entries),
        "entries": entries[:300],
    }


def tool_find_by_name(
    search_directory: str,
    pattern: Optional[str] = None,
    extensions: Optional[List[str]] = None,
    excludes: Optional[List[str]] = None,
    max_depth: Optional[int] = None,
    item_type: Optional[str] = None,
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Busca arquivos e pastas por padrão glob ou extensão a partir de um diretório local."""
    root_dir = resolve_local_path(search_directory, default_workdir or get_default_local_workdir())
    if not os.path.isdir(root_dir):
        return {"error": f"Diretório de busca não encontrado: {root_dir}", "search_directory": root_dir}

    pat = (pattern or "*").strip()
    ext_set = {e.lower().lstrip(".") for e in (extensions or []) if e}
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
    user_excludes = [ex for ex in (excludes or []) if ex]
    matches: List[Dict[str, Any]] = []
    root_depth = root_dir.rstrip(os.sep).count(os.sep)

    for cur_root, dirs, files in os.walk(root_dir):
        cur_depth = cur_root.rstrip(os.sep).count(os.sep) - root_depth
        if max_depth is not None and cur_depth >= int(max_depth):
            dirs[:] = []
        else:
            dirs[:] = [
                d for d in dirs
                if d not in ignore_dirs
                and not any(fnmatch.fnmatch(d, ex) for ex in user_excludes)
            ]

        candidates: List[Tuple[str, bool]] = []
        if item_type in (None, "any", "directory"):
            candidates.extend((d, True) for d in dirs)
        if item_type in (None, "any", "file"):
            candidates.extend((f, False) for f in files)

        for name, is_dir in candidates:
            full_p = os.path.join(cur_root, name)
            rel_p = os.path.relpath(full_p, root_dir).replace("\\", "/")
            if any(fnmatch.fnmatch(rel_p, ex) or fnmatch.fnmatch(name, ex) for ex in user_excludes):
                continue
            if ext_set and not is_dir:
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext not in ext_set:
                    continue
            if pat and pat != "*":
                if not (
                    fnmatch.fnmatch(name.lower(), pat.lower())
                    or fnmatch.fnmatch(rel_p.lower(), pat.lower())
                    or pat.lower() in name.lower()
                ):
                    continue
            try:
                sz = 0 if is_dir else os.path.getsize(full_p)
            except Exception:
                sz = 0
            matches.append({
                "path": full_p,
                "relative_path": rel_p,
                "type": "directory" if is_dir else "file",
                "size_bytes": sz,
            })
            if len(matches) >= 80:
                break
        if len(matches) >= 80:
            break

    return {
        "search_directory": root_dir,
        "pattern": pat,
        "count": len(matches),
        "matches": matches,
    }


def tool_grep_search(
    search_path: str,
    query: str,
    is_regex: bool = False,
    case_insensitive: bool = True,
    includes: Optional[List[str]] = None,
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Procura ocorrências de texto ou expressão regular dentro dos arquivos locais."""
    target = resolve_local_path(search_path, default_workdir or get_default_local_workdir())
    if not os.path.exists(target):
        return {"error": f"Caminho não encontrado: {target}", "search_path": target}
    if not query:
        return {"error": "O parâmetro 'Query' não pode ser vazio.", "search_path": target}

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(query if is_regex else re.escape(query), flags)
    except Exception as exc:
        return {"error": f"Expressão regular inválida: {exc}", "search_path": target}

    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    include_globs = [inc for inc in (includes or []) if inc]
    results: List[Dict[str, Any]] = []

    files_to_scan: List[str] = []
    if os.path.isfile(target):
        files_to_scan.append(target)
    else:
        for r, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for f in files:
                if include_globs and not any(fnmatch.fnmatch(f, g) for g in include_globs):
                    continue
                files_to_scan.append(os.path.join(r, f))

    for fpath in files_to_scan:
        try:
            if os.path.getsize(fpath) > 2 * 1024 * 1024:
                continue
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for line_no, line in enumerate(f, start=1):
                    if regex.search(line):
                        results.append({
                            "file": fpath,
                            "line": line_no,
                            "content": line.rstrip("\r\n")[:300],
                        })
                        if len(results) >= 80:
                            break
        except Exception:
            continue
        if len(results) >= 80:
            break

    return {
        "search_path": target,
        "query": query,
        "count": len(results),
        "matches": results,
    }


async def tool_web_search(queries: List[str]) -> Dict[str, Any]:
    """Pesquisa informações atualizadas na web."""
    all_results: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for q in (queries or [])[:4]:
            q_str = (q or "").strip()
            if not q_str:
                continue
            try:
                url = "https://html.duckduckgo.com/html/"
                resp = await client.post(
                    url,
                    data={"q": q_str},
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AntigravityStudio/1.2"},
                )
                if resp.status_code == 200:
                    snippets = re.findall(
                        r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>',
                        resp.text,
                        flags=re.DOTALL,
                    )
                    items = []
                    for href, title_html, snip_html in snippets[:6]:
                        clean_title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
                        clean_snip = html.unescape(re.sub(r"<[^>]+>", "", snip_html)).strip()
                        items.append({"title": clean_title, "url": href, "snippet": clean_snip})
                    all_results.append({"query": q_str, "results": items})
                else:
                    all_results.append({"query": q_str, "error": f"HTTP {resp.status_code}"})
            except Exception as exc:
                all_results.append({"query": q_str, "error": str(exc)})
    return {"searches": all_results}


async def tool_browse_url(urls: List[str]) -> Dict[str, Any]:
    """Acessa e extrai o conteúdo textual de URLs da web."""
    pages: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        for u in (urls or [])[:4]:
            u_str = (u or "").strip()
            if not u_str:
                continue
            try:
                resp = await client.get(
                    u_str,
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AntigravityStudio/1.2"},
                )
                raw_html = resp.text or ""
                cleaned = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
                cleaned = re.sub(r"<[^>]+>", " ", cleaned)
                cleaned = html.unescape(re.sub(r"\s+", " ", cleaned)).strip()
                pages.append({
                    "url": u_str,
                    "status_code": resp.status_code,
                    "content": cleaned[:20000],
                })
            except Exception as exc:
                pages.append({"url": u_str, "error": str(exc)})
    return {"pages": pages}


# ==============================================================================
# Declaração das Capabilities & Supported Tools (Documentação Antigravity Agent)
# ==============================================================================

ANTIGRAVITY_SUPPORTED_TOOLS_METADATA: List[Dict[str, Any]] = [
    {
        "name": "execute_bash",
        "category": "Terminal & OS Execution",
        "icon": "💻",
        "description": "Executa comandos no terminal / shell do sistema operacional local e retorna stdout, stderr e código de saída.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Comando a ser executado no terminal local.",
                },
                "Cwd": {
                    "type": "string",
                    "description": "Diretório de trabalho para execução do comando (caminho absoluto ou relativo ao projeto).",
                },
                "WaitMsBeforeAsync": {
                    "type": "integer",
                    "description": "Tempo máximo de espera em milissegundos (opcional).",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "view_file",
        "category": "Filesystem Read",
        "icon": "👁️",
        "description": "Lê o conteúdo de um arquivo local do computador com numeração de linhas (1-indexed) e suporte a intervalo StartLine..EndLine.",
        "parameters": {
            "type": "object",
            "properties": {
                "AbsolutePath": {
                    "type": "string",
                    "description": "Caminho do arquivo local (absoluto ou relativo à pasta do projeto).",
                },
                "StartLine": {
                    "type": "integer",
                    "description": "Linha inicial para leitura (1-indexed, opcional).",
                },
                "EndLine": {
                    "type": "integer",
                    "description": "Linha final para leitura (1-indexed, opcional).",
                },
            },
            "required": ["AbsolutePath"],
        },
    },
    {
        "name": "write_to_file",
        "category": "Filesystem Write",
        "icon": "📝",
        "description": "Cria um novo arquivo ou sobrescreve/adiciona conteúdo a um arquivo no disco local, criando pastas pai automaticamente.",
        "parameters": {
            "type": "object",
            "properties": {
                "TargetFile": {
                    "type": "string",
                    "description": "Caminho do arquivo local a ser criado ou gravado.",
                },
                "CodeContent": {
                    "type": "string",
                    "description": "Conteúdo completo a ser gravado no arquivo.",
                },
                "Overwrite": {
                    "type": "boolean",
                    "description": "Se verdadeiro, sobrescreve o arquivo caso já exista.",
                },
                "Append": {
                    "type": "boolean",
                    "description": "Se verdadeiro, adiciona o conteúdo ao final do arquivo existente.",
                },
                "Description": {
                    "type": "string",
                    "description": "Breve descrição do que foi criado ou alterado.",
                },
            },
            "required": ["TargetFile", "CodeContent"],
        },
    },
    {
        "name": "replace_file_content",
        "category": "Filesystem Edit",
        "icon": "✏️",
        "description": "Edita diretamente um trecho de código ou texto dentro de um arquivo existente no projeto local.",
        "parameters": {
            "type": "object",
            "properties": {
                "TargetFile": {
                    "type": "string",
                    "description": "Caminho do arquivo local a ser editado.",
                },
                "TargetContent": {
                    "type": "string",
                    "description": "Trecho exato atual no arquivo que será substituído.",
                },
                "ReplacementContent": {
                    "type": "string",
                    "description": "Novo conteúdo que substituirá o TargetContent.",
                },
                "StartLine": {
                    "type": "integer",
                    "description": "Linha inicial aproximada do bloco (1-indexed, opcional).",
                },
                "EndLine": {
                    "type": "integer",
                    "description": "Linha final aproximada do bloco (1-indexed, opcional).",
                },
                "AllowMultiple": {
                    "type": "boolean",
                    "description": "Se verdadeiro, substitui todas as ocorrências encontradas.",
                },
                "Description": {
                    "type": "string",
                    "description": "Explicação resumida da alteração realizada.",
                },
            },
            "required": ["TargetFile", "TargetContent", "ReplacementContent"],
        },
    },
    {
        "name": "list_dir",
        "category": "Workspace Navigation",
        "icon": "📂",
        "description": "Lista todos os arquivos e subpastas dentro de um diretório local do computador.",
        "parameters": {
            "type": "object",
            "properties": {
                "DirectoryPath": {
                    "type": "string",
                    "description": "Caminho do diretório local a ser listado.",
                },
            },
            "required": ["DirectoryPath"],
        },
    },
    {
        "name": "find_by_name",
        "category": "Workspace Search",
        "icon": "🔍",
        "description": "Busca arquivos e subpastas dentro de um diretório local usando padrões glob ou extensões.",
        "parameters": {
            "type": "object",
            "properties": {
                "SearchDirectory": {
                    "type": "string",
                    "description": "Diretório base para a busca.",
                },
                "Pattern": {
                    "type": "string",
                    "description": "Padrão glob ou parte do nome do arquivo (ex: '*.py', '*test*').",
                },
                "Extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista opcional de extensões sem ponto (ex: ['py', 'js']).",
                },
                "Excludes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Padrões glob a ignorar.",
                },
                "MaxDepth": {
                    "type": "integer",
                    "description": "Profundidade máxima de subpastas.",
                },
                "Type": {
                    "type": "string",
                    "description": "Filtro de tipo: 'file', 'directory' ou 'any'.",
                },
            },
            "required": ["SearchDirectory"],
        },
    },
    {
        "name": "grep_search",
        "category": "Codebase Search",
        "icon": "🔎",
        "description": "Pesquisa texto exato ou expressão regular dentro do conteúdo dos arquivos locais do projeto.",
        "parameters": {
            "type": "object",
            "properties": {
                "SearchPath": {
                    "type": "string",
                    "description": "Diretório ou arquivo onde pesquisar.",
                },
                "Query": {
                    "type": "string",
                    "description": "Texto ou padrão regex a procurar.",
                },
                "IsRegex": {
                    "type": "boolean",
                    "description": "Define se Query é uma expressão regular.",
                },
                "CaseInsensitive": {
                    "type": "boolean",
                    "description": "Ignorar maiúsculas/minúsculas (padrão: true).",
                },
                "Includes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filtros glob de nomes de arquivo (ex: ['*.py', '*.ts']).",
                },
            },
            "required": ["SearchPath", "Query"],
        },
    },
    {
        "name": "web_search",
        "category": "Web Grounding",
        "icon": "🌐",
        "description": "Pesquisa informações e documentações na web.",
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de consultas de pesquisa.",
                },
            },
            "required": ["queries"],
        },
    },
    {
        "name": "browse_url",
        "category": "Web Grounding",
        "icon": "🌍",
        "description": "Extrai o conteúdo textual de uma ou mais URLs públicas.",
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de URLs para ler.",
                },
            },
            "required": ["urls"],
        },
    },
]


def get_interactions_api_tools() -> List[Dict[str, Any]]:
    """Retorna a lista de ferramentas no formato da Interactions API (`type: 'function'`)."""
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        }
        for t in ANTIGRAVITY_SUPPORTED_TOOLS_METADATA
    ]


def get_generate_content_tools() -> List[Dict[str, Any]]:
    """Retorna a lista de ferramentas no formato do endpoint generateContent (`functionDeclarations`)."""
    return [
        {
            "functionDeclarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }
                for t in ANTIGRAVITY_SUPPORTED_TOOLS_METADATA
            ]
        }
    ]


def build_local_agent_system_instruction(
    workdir: str,
    custom_instruction: Optional[str] = None,
) -> str:
    """Constrói a instrução de sistema contextualizada com a máquina e diretório local."""
    abs_workdir = os.path.abspath(workdir or get_default_local_workdir())
    hw = get_local_machine_summary(abs_workdir)

    base_lines = [
        "[DIRETIVA CRÍTICA DE MODO MÁQUINA LOCAL — LEIA COM ATENÇÃO]",
        "Você está operando em MODO MÁQUINA LOCAL conectado diretamente ao computador real do usuário através do ponte de execução local do Antigravity Studio.",
        "ATENÇÃO: Suas ferramentas nativas internas (`default_api:*`) rodam em um container Ubuntu remoto na nuvem do Google e NÃO correspondem à máquina do usuário.",
        "PORTANTO, É ESTRITAMENTE PROIBIDO chamar qualquer ferramenta interna `default_api:*` neste turno!",
        "",
        "Dados Reais da Máquina Local do Usuário Detectados Agora:",
        f"- Sistema Operacional Real do Usuário: {hw.get('os')} ({hw.get('architecture')})",
        f"- Hostname / Usuário Local: {hw.get('hostname', 'N/A')} / {hw.get('username', 'N/A')}",
        f"- Processador / Núcleos Lógicos: {hw.get('processor', 'N/A')} ({hw.get('cpu_cores_logical', 'N/A')} núcleos)",
        f"- Memória RAM Local: {hw.get('ram_total_gb', 'N/A')} GB total ({hw.get('ram_available_gb', 'N/A')} GB disponível)",
        f"- Disco Local: {hw.get('disk_total_gb', 'N/A')} GB total ({hw.get('disk_free_gb', 'N/A')} GB livre)",
        f"- Python Local: {hw.get('Python')}",
        f"- Diretório de Trabalho Atual do Projeto (`local_workdir`): {abs_workdir}",
        "",
        "COMO EXECUTAR COMANDOS E ACESSAR ARQUIVOS NA MÁQUINA LOCAL DO USUÁRIO:",
        "Sempre que você precisar executar um comando no terminal do usuário, ler/criar/editar arquivos locais, listar pastas ou inspecionar detalhes adicionais de hardware/SO/rede/processos, você DEVE responder APENAS com um ou mais blocos XML `<local_tool_call>` contendo um JSON válido no formato:",
        "<local_tool_call>",
        '{"name": "execute_bash", "arguments": {"command": "COMANDO_AQUI", "Cwd": "."}}',
        "</local_tool_call>",
        "",
        "Ferramentas Locais Disponíveis via `<local_tool_call>`:",
        '1. `execute_bash`: `{"name": "execute_bash", "arguments": {"command": "...", "Cwd": "."}}` — executa comandos no terminal real do usuário (Windows PowerShell/CMD ou Linux/macOS bash).',
        '2. `view_file`: `{"name": "view_file", "arguments": {"AbsolutePath": "...", "StartLine": 1, "EndLine": 200}}` — lê arquivos do disco local do usuário.',
        '3. `write_to_file`: `{"name": "write_to_file", "arguments": {"TargetFile": "...", "CodeContent": "...", "Overwrite": true}}` — cria ou sobrescreve arquivos no disco local do usuário.',
        '4. `replace_file_content`: `{"name": "replace_file_content", "arguments": {"TargetFile": "...", "TargetContent": "...", "ReplacementContent": "..."}}` — edita trechos de arquivos locais.',
        '5. `list_dir`: `{"name": "list_dir", "arguments": {"DirectoryPath": "."}}` — lista arquivos e pastas locais do computador do usuário.',
        '6. `find_by_name`: `{"name": "find_by_name", "arguments": {"SearchDirectory": ".", "Pattern": "*"}}` — busca arquivos locais por nome/glob.',
        '7. `grep_search`: `{"name": "grep_search", "arguments": {"SearchPath": ".", "Query": "..."}}` — busca texto ou regex dentro dos arquivos locais.',
        '8. `web_search` / `browse_url`: `{"name": "web_search", "arguments": {"queries": ["..."]}}` / `{"name": "browse_url", "arguments": {"urls": ["..."]}}`.',
        "",
        "REGRAS OBRIGATÓRIAS DO PROTOCOLO `<local_tool_call>`:",
        "- NUNCA invoque `default_api:execute_bash`, `default_api:view_file` etc., pois eles olham para o container remoto e não para o PC do usuário.",
        "- Quando precisar rodar uma ferramenta local, emita SOMENTE o(s) bloco(s) `<local_tool_call>{...}</local_tool_call>` e pare sua resposta imediatamente. O Antigravity Studio executará a ferramenta no PC do usuário e enviará o resultado real no próximo passo para você formular a resposta final.",
        "- Quando o usuário perguntar sobre configurações do computador, hardware (CPU, GPU, placa de vídeo, memória RAM, discos, placa-mãe, bateria, drivers), rede, processos ou arquivos locais, emita um `<local_tool_call>` com `execute_bash` (ex: no Windows `Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors; Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM; Get-CimInstance Win32_DiskDrive | Select-Object Model,Size; Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product`; no Linux `lscpu && free -h && df -h && lspci`).",
        "- Para comandos `sudo` no Linux que pedem senha, você PODE passar a senha de forma 100% não-interativa via pipe `stdin`: `printf '%s\\n' 'SENHA_DO_USUARIO' | sudo -S -p '' <comando>`. NUNCA diga que não consegue fornecer senhas para prompts `sudo`. Se um comando `sudo` falhar pedindo senha e você ainda não souber a senha, pergunte ao usuário; assim que ele informar a senha, execute imediatamente via `<local_tool_call>` com `printf '%s\\n' 'SENHA' | sudo -S -p '' <comando>`.",
    ]
    if custom_instruction and custom_instruction.strip():
        base_lines.extend(["", "Instruções Adicionais do Usuário:", custom_instruction.strip()])
    return "\n".join(base_lines)


async def execute_local_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
    workdir: str,
) -> Tuple[Dict[str, Any], bool]:
    """Executa uma chamada de ferramenta na máquina local e retorna (resultado_dict, is_error)."""
    args = arguments if isinstance(arguments, dict) else {}
    name = (tool_name or "").strip()

    try:
        if name == "execute_bash":
            cmd = str(args.get("command") or args.get("cmd") or "")
            cwd = args.get("Cwd") or args.get("cwd") or workdir
            wait_ms = args.get("WaitMsBeforeAsync")
            timeout_s = max(30, int(wait_ms) // 1000) if wait_ms else 120
            res = await asyncio.to_thread(
                tool_execute_bash,
                command=cmd,
                cwd=cwd,
                timeout_seconds=timeout_s,
                default_workdir=workdir,
            )
            is_err = bool(res.get("error")) or int(res.get("exit_code", 0)) != 0
            return res, is_err

        if name == "view_file":
            fpath = str(
                args.get("AbsolutePath")
                or args.get("file_path")
                or args.get("path")
                or args.get("TargetFile")
                or ""
            )
            s_line = args.get("StartLine") or args.get("start_line")
            e_line = args.get("EndLine") or args.get("end_line")
            res = await asyncio.to_thread(
                tool_view_file,
                file_path=fpath,
                start_line=int(s_line) if s_line is not None else None,
                end_line=int(e_line) if e_line is not None else None,
                default_workdir=workdir,
            )
            return res, bool(res.get("error"))

        if name == "write_to_file":
            tfile = str(
                args.get("TargetFile")
                or args.get("target_file")
                or args.get("path")
                or args.get("file_path")
                or ""
            )
            content = str(args.get("CodeContent") if args.get("CodeContent") is not None else (args.get("content") or ""))
            overwrite = bool(args.get("Overwrite", True))
            append = bool(args.get("Append", False))
            desc = str(args.get("Description") or "")
            res = await asyncio.to_thread(
                tool_write_to_file,
                target_file=tfile,
                code_content=content,
                overwrite=overwrite,
                append=append,
                description=desc,
                default_workdir=workdir,
            )
            return res, bool(res.get("error"))

        if name == "replace_file_content":
            tfile = str(
                args.get("TargetFile")
                or args.get("target_file")
                or args.get("path")
                or ""
            )
            target_c = str(args.get("TargetContent") or args.get("target_content") or "")
            repl_c = str(
                args.get("ReplacementContent")
                if args.get("ReplacementContent") is not None
                else (args.get("replacement_content") or "")
            )
            s_line = args.get("StartLine") or args.get("start_line")
            e_line = args.get("EndLine") or args.get("end_line")
            allow_mult = bool(args.get("AllowMultiple", False))
            desc = str(args.get("Description") or "")
            res = await asyncio.to_thread(
                tool_replace_file_content,
                target_file=tfile,
                target_content=target_c,
                replacement_content=repl_c,
                start_line=int(s_line) if s_line is not None else None,
                end_line=int(e_line) if e_line is not None else None,
                allow_multiple=allow_mult,
                description=desc,
                default_workdir=workdir,
            )
            return res, bool(res.get("error"))

        if name == "list_dir":
            dpath = str(
                args.get("DirectoryPath")
                or args.get("directory_path")
                or args.get("path")
                or "."
            )
            res = await asyncio.to_thread(
                tool_list_dir,
                directory_path=dpath,
                default_workdir=workdir,
            )
            return res, bool(res.get("error"))

        if name == "find_by_name":
            sdir = str(
                args.get("SearchDirectory")
                or args.get("search_directory")
                or args.get("path")
                or "."
            )
            res = await asyncio.to_thread(
                tool_find_by_name,
                search_directory=sdir,
                pattern=args.get("Pattern") or args.get("pattern"),
                extensions=args.get("Extensions") or args.get("extensions"),
                excludes=args.get("Excludes") or args.get("excludes"),
                max_depth=args.get("MaxDepth") or args.get("max_depth"),
                item_type=args.get("Type") or args.get("type"),
                default_workdir=workdir,
            )
            return res, bool(res.get("error"))

        if name == "grep_search":
            spath = str(
                args.get("SearchPath")
                or args.get("search_path")
                or args.get("path")
                or "."
            )
            query = str(args.get("Query") or args.get("query") or "")
            res = await asyncio.to_thread(
                tool_grep_search,
                search_path=spath,
                query=query,
                is_regex=bool(args.get("IsRegex", False)),
                case_insensitive=bool(args.get("CaseInsensitive", True)),
                includes=args.get("Includes") or args.get("includes"),
                default_workdir=workdir,
            )
            return res, bool(res.get("error"))

        if name in ("web_search", "google_search"):
            queries = args.get("queries") or ([args["query"]] if args.get("query") else [])
            res = await tool_web_search(queries)
            return res, bool(res.get("error"))

        if name in ("browse_url", "browse"):
            urls = args.get("urls") or ([args["url"]] if args.get("url") else [])
            res = await tool_browse_url(urls)
            return res, bool(res.get("error"))

        return {"error": f"Ferramenta desconhecida: {name}"}, True
    except Exception as exc:
        return {"error": f"Exceção ao executar ferramenta '{name}': {exc}"}, True


def _extract_function_calls_from_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if s.get("type") == "function_call":
            calls.append({
                "id": s.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                "name": s.get("name") or "",
                "arguments": s.get("arguments") if isinstance(s.get("arguments"), dict) else {},
            })
    return calls


def _sum_usage_dicts(accum: Dict[str, int], new_u: Dict[str, Any]) -> None:
    if not isinstance(new_u, dict):
        return
    for k in ("total_tokens", "total_input_tokens", "total_output_tokens", "total_thought_tokens", "total_cached_tokens"):
        val = new_u.get(k)
        if isinstance(val, (int, float)):
            accum[k] = accum.get(k, 0) + int(val)


def _format_turn_history_snippet(t: Dict[str, Any]) -> str:
    """Formata um turno anterior incluindo as ferramentas locais executadas para manter contexto."""
    ut = (t.get("user_input_text") or "").strip()
    ot = (t.get("output_text") or "").strip()
    steps = t.get("steps") if isinstance(t.get("steps"), list) else []
    tool_logs: List[str] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        st = s.get("type")
        if st == "function_call":
            fn = s.get("name") or "tool"
            fargs = json.dumps(s.get("arguments") or {}, ensure_ascii=False)[:240]
            tool_logs.append(f"  -> Chamou ferramenta local `{fn}` com {fargs}")
        elif st == "function_result":
            fn = s.get("name") or "tool"
            fres = json.dumps(s.get("result") or {}, ensure_ascii=False)[:300]
            tool_logs.append(f"  <- Resultado de `{fn}`: {fres}")
    parts = [f"Usuário: {ut[:800]}"]
    if tool_logs:
        parts.append("Ações executadas pelo Agente Local na máquina do usuário:\n" + "\n".join(tool_logs[:12]))
    if ot:
        parts.append(f"Resposta do Agente Local: {ot[:1200]}")
    return "\n".join(parts)


_LOCAL_TOOL_CALL_RE = re.compile(
    r"<local_tool_call>\s*(.*?)\s*</local_tool_call>",
    re.DOTALL | re.IGNORECASE,
)


def _extract_local_tool_calls_from_text(text: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extrai blocos `<local_tool_call>{...}</local_tool_call>` emitidos pelo `gemini-4-argon`
    e retorna (lista_de_chamadas, texto_limpo_sem_os_blocos).
    """
    if not text or "<local_tool_call>" not in text.lower():
        return [], (text or "").strip()

    calls: List[Dict[str, Any]] = []
    for match in _LOCAL_TOOL_CALL_RE.finditer(text):
        raw_inner = (match.group(1) or "").strip()
        if raw_inner.startswith("```"):
            raw_inner = re.sub(r"^```(?:json)?\s*", "", raw_inner, flags=re.IGNORECASE)
            raw_inner = re.sub(r"\s*```$", "", raw_inner)
        try:
            parsed = json.loads(raw_inner)
        except Exception:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict):
                continue
            tname = (item.get("name") or item.get("tool") or "").strip()
            targs = item.get("arguments") if isinstance(item.get("arguments"), dict) else (
                item.get("args") if isinstance(item.get("args"), dict) else {}
            )
            if tname:
                calls.append({
                    "id": item.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                    "name": tname,
                    "arguments": targs,
                })

    cleaned_text = _LOCAL_TOOL_CALL_RE.sub("", text).strip()
    return calls, cleaned_text


async def _run_antigravity_agent_hop(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    model_name: str,
    hop_input: str,
    previous_interaction_id: Optional[str],
    run_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Executa um turno (hop) usando SEMPRE o agente `antigravity-preview-09-2026`
    com `agent_config.model = "gemini-4-argon"` (ou o modelo configurado em `agent_config.model`)
    na Interactions API (`POST /v1beta/interactions`), realizando polling até concluir.
    """
    underlying_model = "gemini-4-argon"
    payload: Dict[str, Any] = {
        "agent": "antigravity-preview-09-2026",
        "environment": "remote",
        "agent_config": {
            "type": "antigravity",
            "model": underlying_model,
        },
        "input": hop_input,
        "background": True,
        "store": True,
    }
    if previous_interaction_id and previous_interaction_id.startswith("v1_"):
        payload["previous_interaction_id"] = previous_interaction_id

    resp = await client.post(INTERACTIONS_BASE_URL, json=payload, headers=headers)
    if resp.status_code in (400, 404) and payload.get("previous_interaction_id"):
        payload.pop("previous_interaction_id", None)
        resp = await client.post(INTERACTIONS_BASE_URL, json=payload, headers=headers)

    if resp.status_code >= 400:
        raise ValueError(
            f"Erro na Interactions API ({underlying_model}, HTTP {resp.status_code}): {resp.text}"
        )

    data = resp.json()
    interaction_id = data.get("id")
    status = data.get("status", "")

    poll_count = 0
    while status == "in_progress" and interaction_id and poll_count < 150:
        if run_state.get("cancelled"):
            try:
                await client.post(f"{INTERACTIONS_BASE_URL}/{interaction_id}/cancel", headers=headers)
            except Exception:
                pass
            data["status"] = "cancelled"
            return data
        await asyncio.sleep(1.5)
        poll_count += 1
        poll_resp = await client.get(f"{INTERACTIONS_BASE_URL}/{interaction_id}", headers=headers)
        if poll_resp.status_code < 400:
            data = poll_resp.json()
            status = data.get("status", "")
        else:
            break

    return data


async def run_local_agent_turn(
    prompt: str,
    workdir: str,
    model_name: str = "gemini-4-argon",
    previous_interaction_id: Optional[str] = None,
    system_instruction: Optional[str] = None,
    history_turns: Optional[List[Dict[str, Any]]] = None,
    api_key: Optional[str] = None,
    max_iterations: int = 12,
    interaction_id_override: Optional[str] = None,
    on_progress: Optional[Callable[[List[Dict[str, Any]], str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Executa um turno completo do Agente Local na máquina do usuário:
    - Usa SEMPRE o modelo subjacente `gemini-4-argon` (`agent_config.model`) através do agente
      `antigravity-preview-09-2026` na Interactions API (`POST /v1beta/interactions`).
    - Injeta as informações reais da máquina local do usuário e o protocolo `<local_tool_call>`
      para que todas as ferramentas (`execute_bash`, `view_file`, `write_to_file`,
      `replace_file_content`, `list_dir`, `find_by_name`, `grep_search`, `web_search`, `browse_url`)
      sejam executadas diretamente no computador do usuário (`workdir`) e seus resultados reais
      sejam devolvidos ao `gemini-4-argon` até concluir a resposta final.
    """
    abs_workdir = resolve_local_path(".", workdir or get_default_local_workdir())
    os.makedirs(abs_workdir, exist_ok=True)

    local_turn_id = interaction_id_override or f"local_{uuid.uuid4().hex[:16]}"
    run_state: Dict[str, Any] = {
        "id": local_turn_id,
        "status": "in_progress",
        "workdir": abs_workdir,
        "steps": [
            {
                "type": "user_input",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
        "output_text": "",
        "usage": {},
        "cancelled": False,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    ACTIVE_LOCAL_RUNS[local_turn_id] = run_state

    sys_inst = build_local_agent_system_instruction(abs_workdir, system_instruction)
    accumulated_steps: List[Dict[str, Any]] = list(run_state["steps"])
    accumulated_usage: Dict[str, int] = {}
    final_output_text = ""
    final_status = "completed"
    final_api_id = local_turn_id

    def _notify(steps_now: List[Dict[str, Any]], out_now: str, status_now: str) -> None:
        run_state["steps"] = list(steps_now)
        run_state["output_text"] = out_now
        run_state["status"] = status_now
        run_state["usage"] = dict(accumulated_usage)
        if on_progress:
            try:
                on_progress(list(steps_now), out_now, status_now)
            except Exception:
                pass

    # Resolve previous_interaction_id para o ID real `v1_...` da Interactions API se disponível
    clean_prev = (previous_interaction_id or "").strip()
    if clean_prev.startswith("local_") and history_turns:
        for t in reversed(history_turns):
            if t.get("interaction_id") == clean_prev:
                raw_api_id = ((t.get("raw") or {}).get("_api_interaction_id") or "").strip()
                if raw_api_id.startswith("v1_"):
                    clean_prev = raw_api_id
                break
    can_chain_prev_id = clean_prev.startswith("v1_")

    history_block = ""
    if not can_chain_prev_id and history_turns:
        hist_snippets: List[str] = []
        for t in history_turns[-6:]:
            if t.get("status") in ("completed", "incomplete"):
                snippet = _format_turn_history_snippet(t)
                if snippet.strip():
                    hist_snippets.append(snippet)
        if hist_snippets:
            history_block = (
                "\n\n[HISTÓRICO DE TURNOS E FERRAMENTAS EXECUTADAS NA SESSÃO LOCAL]:\n"
                + "\n---\n".join(hist_snippets)
            )

    initial_hop_input = (
        f"{sys_inst}"
        f"{history_block}\n\n"
        f"[PEDIDO ATUAL DO USUÁRIO NA MÁQUINA LOCAL]:\n{prompt}"
    )

    headers = build_headers(api_key)
    underlying_model = "gemini-4-argon"

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            current_hop_input = initial_hop_input
            current_prev_id: Optional[str] = clean_prev if can_chain_prev_id else None

            for hop in range(max_iterations):
                if run_state.get("cancelled"):
                    final_status = "cancelled"
                    break

                hop_data = await _run_antigravity_agent_hop(
                    client=client,
                    headers=headers,
                    model_name=underlying_model,
                    hop_input=current_hop_input,
                    previous_interaction_id=current_prev_id,
                    run_state=run_state,
                )

                if hop_data.get("id"):
                    final_api_id = str(hop_data["id"])
                    current_prev_id = final_api_id

                _sum_usage_dicts(accumulated_usage, hop_data.get("usage") or {})

                hop_steps = hop_data.get("steps") or []
                hop_text_candidates: List[str] = []
                for s in hop_steps:
                    if not isinstance(s, dict):
                        continue
                    stype = s.get("type", "")
                    if stype == "user_input":
                        continue
                    if stype == "thought":
                        accumulated_steps.append(s)
                    elif stype == "model_output":
                        txts = [
                            c.get("text", "")
                            for c in (s.get("content") or [])
                            if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
                        ]
                        if txts:
                            hop_text_candidates.append("\n\n".join(txts).strip())

                raw_hop_output = (
                    hop_text_candidates[-1]
                    if hop_text_candidates
                    else (hop_data.get("output_text") or "").strip()
                )
                if not raw_hop_output:
                    outputs_arr = hop_data.get("outputs") or []
                    out_txts = [
                        o.get("text", "")
                        for o in outputs_arr
                        if isinstance(o, dict) and o.get("type") == "text" and o.get("text")
                    ]
                    if out_txts:
                        raw_hop_output = "\n\n".join(out_txts).strip()

                local_calls, cleaned_output = _extract_local_tool_calls_from_text(raw_hop_output)

                # Caso o modelo tenha chamado por engano alguma tool interna no 1º hop em vez de <local_tool_call>,
                # interceptamos os argumentos dessas function_calls e as executamos na máquina local do usuário!
                if not local_calls and hop == 0:
                    internal_fcs = _extract_function_calls_from_steps(hop_steps)
                    if internal_fcs:
                        local_calls = internal_fcs

                if not local_calls:
                    final_output_text = cleaned_output or raw_hop_output
                    if final_output_text:
                        accumulated_steps.append({
                            "type": "model_output",
                            "content": [{"type": "text", "text": final_output_text}],
                        })
                    final_status = hop_data.get("status") or "completed"
                    if final_status in ("requires_action", "in_progress"):
                        final_status = "completed"
                    _notify(accumulated_steps, final_output_text, final_status)
                    break

                if cleaned_output:
                    accumulated_steps.append({
                        "type": "thought",
                        "content": [{"type": "text", "text": cleaned_output}],
                    })

                tool_results_feedback: List[str] = []
                for fc in local_calls:
                    if run_state.get("cancelled"):
                        final_status = "cancelled"
                        break
                    accumulated_steps.append({
                        "type": "function_call",
                        "id": fc["id"],
                        "name": fc["name"],
                        "arguments": fc["arguments"],
                    })
                    _notify(accumulated_steps, final_output_text, "in_progress")

                    tool_res, is_err = await execute_local_tool_call(
                        tool_name=fc["name"],
                        arguments=fc["arguments"],
                        workdir=abs_workdir,
                    )
                    accumulated_steps.append({
                        "type": "function_result",
                        "call_id": fc["id"],
                        "name": fc["name"],
                        "result": tool_res,
                        "is_error": is_err,
                    })
                    _notify(accumulated_steps, final_output_text, "in_progress")

                    res_json_str = json.dumps(tool_res, ensure_ascii=False)
                    if len(res_json_str) > 24000:
                        res_json_str = res_json_str[:24000] + "... [truncado]"
                    tool_results_feedback.append(
                        f"<local_tool_result name=\"{fc['name']}\" is_error=\"{str(is_err).lower()}\">\n"
                        f"{res_json_str}\n"
                        f"</local_tool_result>"
                    )

                if run_state.get("cancelled"):
                    final_status = "cancelled"
                    break

                current_hop_input = (
                    "[RESULTADO REAL DA EXECUÇÃO NA MÁQUINA LOCAL DO USUÁRIO]:\n"
                    + "\n\n".join(tool_results_feedback)
                    + "\n\n[INSTRUÇÃO]: Se precisar executar mais alguma ferramenta local na máquina do usuário, "
                    "emita apenas o próximo bloco `<local_tool_call>{...}</local_tool_call>`. "
                    "Caso já tenha todos os dados necessários, apresente a resposta final completa e detalhada "
                    "ao usuário com base exclusivamente nos resultados reais acima (NÃO chame ferramentas internas `default_api:*`)."
                )

    except Exception as exc:
        final_status = "failed"
        if not final_output_text:
            final_output_text = f"❌ Erro durante a execução do Agente Local ({underlying_model}): {exc}"
        accumulated_steps.append({
            "type": "model_output",
            "content": [{"type": "text", "text": final_output_text}],
        })

    _notify(accumulated_steps, final_output_text, final_status)
    result_payload = {
        "id": final_api_id if not interaction_id_override else local_turn_id,
        "_api_interaction_id": final_api_id,
        "previous_interaction_id": clean_prev,
        "environment_id": f"local:{abs_workdir}",
        "execution_mode": "local",
        "local_workdir": abs_workdir,
        "status": final_status,
        "steps": accumulated_steps,
        "output_text": final_output_text,
        "usage": accumulated_usage,
        "created": run_state["created"],
    }
    ACTIVE_LOCAL_RUNS[local_turn_id] = {**run_state, **result_payload}
    return result_payload

