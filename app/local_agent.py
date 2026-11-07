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


def tool_execute_bash(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: int = 120,
    default_workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Executa comando no terminal do sistema operacional local."""
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
        proc = subprocess.run(
            cmd_str,
            shell=True,
            cwd=effective_cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_val,
            env=env,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        stdout_txt = proc.stdout or ""
        stderr_txt = proc.stderr or ""
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
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    py_ver = sys.version.split()[0]
    abs_workdir = os.path.abspath(workdir or get_default_local_workdir())

    base_lines = [
        "Você é o Agente Antigravity operando em Modo Máquina Local (CLI / Local Workspace Agent).",
        f"- Sistema Operacional Local: {os_info}",
        f"- Python Local: {py_ver}",
        f"- Diretório de Trabalho do Projeto (`local_workdir`): {abs_workdir}",
        "",
        "Suas Capabilities e Supported Tools ativas na máquina local:",
        "1. `execute_bash`: executa comandos diretamente no terminal do sistema operacional local.",
        "2. `view_file`: lê arquivos locais com numeração de linhas (StartLine..EndLine).",
        "3. `write_to_file`: cria novos arquivos ou sobrescreve arquivos e pastas no disco local.",
        "4. `replace_file_content`: edita blocos específicos de código/texto em arquivos locais.",
        "5. `list_dir`: lista arquivos e subpastas de qualquer diretório local.",
        "6. `find_by_name`: localiza arquivos por nome, padrão glob ou extensão.",
        "7. `grep_search`: busca código, funções ou textos dentro dos arquivos do projeto local.",
        "8. `web_search` e `browse_url`: pesquisa na web e extrai conteúdo de páginas.",
        "",
        "Diretrizes de Execução Local:",
        "- Ao criar, ler ou editar arquivos com caminhos relativos, eles serão resolvidos a partir do diretório do projeto local.",
        "- Prefira usar `view_file`, `list_dir`, `find_by_name` e `grep_search` para inspecionar o código e `write_to_file` ou `replace_file_content` para criar e editar arquivos.",
        "- Ao usar `execute_bash`, execute comandos não-interativos adequados ao sistema operacional local.",
        "- Sempre informe de forma clara e objetiva quais arquivos foram criados/modificados e o resultado dos comandos executados.",
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


async def _run_local_agent_via_generate_content(
    prompt: str,
    model_name: str,
    sys_instruction: str,
    workdir: str,
    history_turns: Optional[List[Dict[str, Any]]],
    accumulated_steps: List[Dict[str, Any]],
    accumulated_usage: Dict[str, int],
    run_state: Dict[str, Any],
    on_progress: Optional[Callable[[List[Dict[str, Any]], str, str], None]],
    max_iterations: int,
    api_key: Optional[str],
) -> Tuple[str, str]:
    """
    Executa o loop de Function Calling do Agente Local usando o endpoint padrão
    `POST /v1beta/models/{model}:generateContent` caso o modelo selecionado ou a
    Interactions API exija chamada direta.
    """
    effective_model = model_name or "gemini-2.5-flash"
    if effective_model.startswith("models/"):
        effective_model = effective_model.replace("models/", "", 1)

    contents: List[Dict[str, Any]] = []
    for t in (history_turns or [])[-8:]:
        if t.get("status") in ("completed", "incomplete"):
            ut = (t.get("user_input_text") or "").strip()
            ot = (t.get("output_text") or "").strip()
            if ut:
                contents.append({"role": "user", "parts": [{"text": ut}]})
            if ot:
                contents.append({"role": "model", "parts": [{"text": ot}]})

    contents.append({"role": "user", "parts": [{"text": prompt}]})
    tools_payload = get_generate_content_tools()
    headers = build_headers(api_key)
    final_texts: List[str] = []

    fallback_models = [effective_model]
    for candidate in ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"):
        if candidate not in fallback_models:
            fallback_models.append(candidate)

    active_model = fallback_models[0]

    async with httpx.AsyncClient(timeout=180.0) as client:
        for iteration in range(max_iterations):
            if run_state.get("cancelled"):
                return "\n\n".join(final_texts).strip(), "cancelled"

            req_body: Dict[str, Any] = {
                "contents": contents,
                "tools": tools_payload,
                "systemInstruction": {"parts": [{"text": sys_instruction}]},
            }

            resp = None
            for m_idx, m_try in enumerate(fallback_models if iteration == 0 else [active_model]):
                url = f"{GENERATE_CONTENT_BASE_URL}/{m_try}:generateContent"
                resp = await client.post(url, json=req_body, headers=headers)
                if resp.status_code < 400:
                    active_model = m_try
                    break
                if resp.status_code in (400, 404) and m_idx < len(fallback_models) - 1:
                    continue

            if resp is None or resp.status_code >= 400:
                err_txt = resp.text if resp is not None else "Sem resposta HTTP"
                code = resp.status_code if resp is not None else 500
                raise ValueError(f"Erro na API Gemini ({active_model}, HTTP {code}): {err_txt}")

            data = resp.json()
            u_meta = data.get("usageMetadata") or {}
            if u_meta:
                accumulated_usage["total_tokens"] = accumulated_usage.get("total_tokens", 0) + int(u_meta.get("totalTokenCount", 0))
                accumulated_usage["total_input_tokens"] = accumulated_usage.get("total_input_tokens", 0) + int(u_meta.get("promptTokenCount", 0))
                accumulated_usage["total_output_tokens"] = accumulated_usage.get("total_output_tokens", 0) + int(u_meta.get("candidatesTokenCount", 0))

            candidates = data.get("candidates") or []
            if not candidates:
                break
            cand_content = (candidates[0].get("content") or {})
            parts = cand_content.get("parts") or []
            contents.append({"role": "model", "parts": parts})

            func_calls: List[Dict[str, Any]] = []
            step_texts: List[str] = []

            for p in parts:
                if not isinstance(p, dict):
                    continue
                if p.get("thought") and p.get("text"):
                    accumulated_steps.append({
                        "type": "thought",
                        "content": [{"type": "text", "text": p["text"]}],
                    })
                elif "functionCall" in p:
                    fc = p["functionCall"]
                    call_id = fc.get("id") or f"call_{uuid.uuid4().hex[:10]}"
                    fc_name = fc.get("name") or ""
                    fc_args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                    func_calls.append({"id": call_id, "name": fc_name, "arguments": fc_args})
                    accumulated_steps.append({
                        "type": "function_call",
                        "id": call_id,
                        "name": fc_name,
                        "arguments": fc_args,
                    })
                elif p.get("text"):
                    step_texts.append(p["text"])

            if step_texts:
                joined_t = "\n".join(step_texts).strip()
                if joined_t:
                    final_texts = [joined_t]
                    accumulated_steps.append({
                        "type": "model_output",
                        "content": [{"type": "text", "text": joined_t}],
                    })

            if on_progress:
                on_progress(accumulated_steps, "\n\n".join(final_texts).strip(), "in_progress")

            if not func_calls:
                break

            response_parts: List[Dict[str, Any]] = []
            for fc in func_calls:
                if run_state.get("cancelled"):
                    return "\n\n".join(final_texts).strip(), "cancelled"
                tool_res, is_err = await execute_local_tool_call(
                    tool_name=fc["name"],
                    arguments=fc["arguments"],
                    workdir=workdir,
                )
                accumulated_steps.append({
                    "type": "function_result",
                    "call_id": fc["id"],
                    "name": fc["name"],
                    "result": tool_res,
                    "is_error": is_err,
                })
                response_parts.append({
                    "functionResponse": {
                        "name": fc["name"],
                        "response": tool_res,
                    }
                })
                if on_progress:
                    on_progress(accumulated_steps, "\n\n".join(final_texts).strip(), "in_progress")

            contents.append({"role": "user", "parts": response_parts})

    return "\n\n".join(final_texts).strip(), "completed"


async def run_local_agent_turn(
    prompt: str,
    workdir: str,
    model_name: str = "gemini-3.8-flash",
    previous_interaction_id: Optional[str] = None,
    system_instruction: Optional[str] = None,
    history_turns: Optional[List[Dict[str, Any]]] = None,
    api_key: Optional[str] = None,
    max_iterations: int = 25,
    interaction_id_override: Optional[str] = None,
    on_progress: Optional[Callable[[List[Dict[str, Any]], str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Executa um turno completo do Agente Local na máquina do usuário:
    - Tenta primeiramente via Interactions API (`POST /v1beta/interactions`) com `model`,
      `previous_interaction_id` (se for um ID válido da Interactions API `v1_...`),
      `system_instruction` e as ferramentas locais (`execute_bash`, `view_file`, `write_to_file`,
      `replace_file_content`, `list_dir`, `find_by_name`, `grep_search`, `web_search`, `browse_url`).
    - A cada `function_call` retornada pelo modelo, executa a ferramenta localmente no disco/terminal
      do usuário (`workdir`) e devolve `function_result` para a API até concluir a resposta.
    - Caso a Interactions API retorne erro para o modelo ou o `previous_interaction_id` seja local,
      faz fallback transparente para `generateContent` mantendo todo o histórico e ferramentas.
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

    # Verifica se previous_interaction_id é um ID remoto da Interactions API (ex: v1_...)
    clean_prev = (previous_interaction_id or "").strip()
    can_chain_prev_id = clean_prev.startswith("v1_")

    effective_prompt = prompt
    if not can_chain_prev_id and history_turns:
        hist_snippets: List[str] = []
        for t in history_turns[-6:]:
            if t.get("status") in ("completed", "incomplete"):
                ut = (t.get("user_input_text") or "").strip()
                ot = (t.get("output_text") or "").strip()
                if ut or ot:
                    hist_snippets.append(f"Usuário: {ut[:600]}\nAgente Local: {ot[:1200]}")
        if hist_snippets:
            effective_prompt = (
                "[HISTÓRICO RESUMIDO DA SESSÃO LOCAL]:\n"
                + "\n---\n".join(hist_snippets)
                + f"\n\n[NOVA INSTRUÇÃO DO USUÁRIO]:\n{prompt}"
            )

    tools_decl = get_interactions_api_tools()
    headers = build_headers(api_key)
    used_fallback = False

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            current_input: Any = effective_prompt
            current_prev_id: Optional[str] = clean_prev if can_chain_prev_id else None

            for hop in range(max_iterations):
                if run_state.get("cancelled"):
                    final_status = "cancelled"
                    break

                hop_payload: Dict[str, Any] = {
                    "model": model_name or "gemini-3.8-flash",
                    "input": current_input,
                    "system_instruction": sys_inst,
                    "tools": tools_decl,
                    "store": True,
                }
                if current_prev_id:
                    hop_payload["previous_interaction_id"] = current_prev_id

                resp = await client.post(
                    INTERACTIONS_BASE_URL,
                    json=hop_payload,
                    headers=headers,
                )

                # Se previous_interaction_id expirou ou era de um agente remoto incompatível com model+tools,
                # tenta novamente sem previous_interaction_id injetando o histórico
                if resp.status_code in (400, 404) and current_prev_id and hop == 0:
                    current_prev_id = None
                    hop_payload.pop("previous_interaction_id", None)
                    if history_turns:
                        hist_snippets = [
                            f"Usuário: {(t.get('user_input_text') or '')[:600]}\nAgente: {(t.get('output_text') or '')[:1200]}"
                            for t in history_turns[-6:]
                            if t.get("status") in ("completed", "incomplete")
                        ]
                        if hist_snippets:
                            hop_payload["input"] = (
                                "[HISTÓRICO RESUMIDO DA SESSÃO]:\n"
                                + "\n---\n".join(hist_snippets)
                                + f"\n\n[NOVA INSTRUÇÃO DO USUÁRIO]:\n{prompt}"
                            )
                    resp = await client.post(
                        INTERACTIONS_BASE_URL,
                        json=hop_payload,
                        headers=headers,
                    )

                if resp.status_code >= 400:
                    if hop == 0:
                        used_fallback = True
                        break
                    raise ValueError(
                        f"Erro na Interactions API durante execução local (HTTP {resp.status_code}): {resp.text}"
                    )

                hop_data = resp.json()
                if hop_data.get("id"):
                    final_api_id = str(hop_data["id"])
                    current_prev_id = final_api_id

                _sum_usage_dicts(accumulated_usage, hop_data.get("usage") or {})

                hop_steps = hop_data.get("steps") or []
                for s in hop_steps:
                    if not isinstance(s, dict):
                        continue
                    stype = s.get("type", "")
                    if stype == "user_input":
                        continue
                    accumulated_steps.append(s)
                    if stype == "model_output":
                        txts = [
                            c.get("text", "")
                            for c in (s.get("content") or [])
                            if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
                        ]
                        if txts:
                            final_output_text = "\n\n".join(txts).strip()

                if not final_output_text and isinstance(hop_data.get("output_text"), str) and hop_data["output_text"].strip():
                    final_output_text = hop_data["output_text"].strip()

                _notify(accumulated_steps, final_output_text, "in_progress")

                func_calls = _extract_function_calls_from_steps(hop_steps)
                if not func_calls:
                    final_status = hop_data.get("status") or "completed"
                    if final_status == "requires_action":
                        final_status = "completed"
                    break

                # Executa cada ferramenta solicitada localmente na máquina do usuário
                result_steps: List[Dict[str, Any]] = []
                for fc in func_calls:
                    if run_state.get("cancelled"):
                        final_status = "cancelled"
                        break
                    tool_res, is_err = await execute_local_tool_call(
                        tool_name=fc["name"],
                        arguments=fc["arguments"],
                        workdir=abs_workdir,
                    )
                    res_str = json.dumps(tool_res, ensure_ascii=False)
                    step_record = {
                        "type": "function_result",
                        "call_id": fc["id"],
                        "name": fc["name"],
                        "result": tool_res,
                        "is_error": is_err,
                    }
                    accumulated_steps.append(step_record)
                    result_steps.append({
                        "type": "function_result",
                        "call_id": fc["id"],
                        "name": fc["name"],
                        "result": res_str,
                        "is_error": is_err,
                    })
                    _notify(accumulated_steps, final_output_text, "in_progress")

                if run_state.get("cancelled"):
                    final_status = "cancelled"
                    break

                current_input = result_steps

        if used_fallback and not run_state.get("cancelled"):
            final_output_text, final_status = await _run_local_agent_via_generate_content(
                prompt=prompt,
                model_name=model_name,
                sys_instruction=sys_inst,
                workdir=abs_workdir,
                history_turns=history_turns,
                accumulated_steps=accumulated_steps,
                accumulated_usage=accumulated_usage,
                run_state=run_state,
                on_progress=_notify,
                max_iterations=max_iterations,
                api_key=api_key,
            )

    except Exception as exc:
        final_status = "failed"
        if not final_output_text:
            final_output_text = f"❌ Erro durante a execução do Agente Local: {exc}"
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
