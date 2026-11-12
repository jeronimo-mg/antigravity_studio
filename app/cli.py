import argparse
import asyncio
import json
import os
import platform
import sys
from typing import Any, Dict, List, Optional

from .database import (
    create_session,
    get_session,
    init_db,
    list_sessions,
    set_setting,
    update_session,
    upsert_turn,
)
from .interactions_client import (
    build_create_payload,
    clean_environment_id,
    create_interaction,
    extract_texts_from_interaction,
    get_interaction,
    is_default_session_title,
    resolve_api_key,
    synthesize_agent_session_title,
)
from .local_agent import (
    ANTIGRAVITY_SUPPORTED_TOOLS_METADATA,
    get_default_local_workdir,
    resolve_local_path,
    run_local_agent_turn,
)


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"


def _print_banner(execution_mode: str, workdir: str, model_name: str, session_id: str) -> None:
    os_label = f"{platform.system()} {platform.release()}"
    mode_label = (
        f"{Colors.GREEN}💻 Máquina Local (CLI / Local OS & Filesystem){Colors.RESET}"
        if execution_mode == "local"
        else f"{Colors.MAGENTA}🌐 Sandbox Remoto (Google Cloud Container){Colors.RESET}"
    )
    print(f"\n{Colors.BOLD}{Colors.CYAN}╭──────────────────────────────────────────────────────────────────────────╮{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}│  🚀 Antigravity Studio CLI — Agente Local & Playground Stateful          │{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}╰──────────────────────────────────────────────────────────────────────────╯{Colors.RESET}")
    print(f"  • {Colors.BOLD}Modo Ativo:{Colors.RESET}      {mode_label}")
    print(f"  • {Colors.BOLD}Pasta Local:{Colors.RESET}     {Colors.YELLOW}{workdir}{Colors.RESET}")
    print(f"  • {Colors.BOLD}Sistema:{Colors.RESET}         {os_label} | Modelo: {Colors.BLUE}{model_name}{Colors.RESET}")
    print(f"  • {Colors.BOLD}Sessão SQLite:{Colors.RESET}   {session_id}")
    print(f"  • {Colors.DIM}Comandos: /help, /tools, /cd <pasta>, /pwd, /ls, /local, /remote, /model <m>, /exit{Colors.RESET}\n")


def _print_tools_list() -> None:
    print(f"\n{Colors.BOLD}{Colors.CYAN}🔧 Capabilities & Supported Tools (Antigravity Agent):{Colors.RESET}")
    for t in ANTIGRAVITY_SUPPORTED_TOOLS_METADATA:
        print(f"  {t['icon']} {Colors.BOLD}{t['name']:<22}{Colors.RESET} [{t['category']}] — {t['description']}")
    print()


def _format_step_for_cli(step: Dict[str, Any]) -> str:
    stype = step.get("type", "")
    if stype == "thought":
        txts = [
            c.get("text", "")
            for c in (step.get("content") or [])
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        joined = "\n".join(txts).strip()
        if joined:
            preview = joined[:300] + ("..." if len(joined) > 300 else "")
            return f"{Colors.DIM}💭 [Pensamento]: {preview}{Colors.RESET}"
    elif stype == "function_call":
        name = step.get("name", "tool")
        args = step.get("arguments") or {}
        summary = ""
        if name == "execute_bash":
            summary = f"$ {args.get('command', '')}"
        elif name in ("view_file", "write_to_file", "replace_file_content"):
            summary = str(args.get("AbsolutePath") or args.get("TargetFile") or args.get("path") or "")
        elif name in ("list_dir", "find_by_name", "grep_search"):
            summary = str(
                args.get("DirectoryPath")
                or args.get("SearchDirectory")
                or args.get("SearchPath")
                or "."
            )
            if args.get("Query") or args.get("Pattern"):
                summary += f" ({args.get('Query') or args.get('Pattern')})"
        else:
            summary = json.dumps(args, ensure_ascii=False)[:120]
        return f"{Colors.YELLOW}🔧 [{name}]{Colors.RESET} {Colors.BOLD}{summary}{Colors.RESET}"
    elif stype == "function_result":
        name = step.get("name", "tool")
        is_err = step.get("is_error", False)
        res = step.get("result")
        status_icon = f"{Colors.RED}✗" if is_err else f"{Colors.GREEN}✓"
        detail = ""
        if isinstance(res, dict):
            if "exit_code" in res:
                detail = f"exit={res.get('exit_code')} ({res.get('duration_ms', 0)}ms)"
                out_preview = (res.get("stdout") or res.get("stderr") or "").strip().splitlines()
                if out_preview:
                    detail += f" -> {out_preview[0][:100]}"
            elif "status" in res and "path" in res:
                detail = f"{res.get('status')}: {res.get('path')} ({res.get('size_bytes', 0)} B)"
            elif "total_lines" in res:
                detail = f"{res.get('path')} ({res.get('total_lines')} linhas)"
            elif "count" in res:
                detail = f"{res.get('count')} item(ns)"
            elif "error" in res:
                detail = str(res.get("error"))[:120]
        return f"  {status_icon} Resultado {name}:{Colors.RESET} {Colors.DIM}{detail}{Colors.RESET}"
    return ""


async def execute_cli_prompt(
    prompt: str,
    session_id: str,
    execution_mode: str,
    workdir: str,
    model_name: str,
    agent_name: str = "antigravity-preview-09-2026",
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    session = get_session(session_id) or create_session(
        title=prompt[:48],
        mode="agent",
        agent_name=agent_name,
        model_name=model_name,
        execution_mode=execution_mode,
        local_workdir=workdir,
    )
    turns = session.get("turns") or []
    prev_id = session.get("last_interaction_id") or ""
    next_index = len(turns) + 1

    if execution_mode == "local":
        printed_steps_count = 1  # pula user_input inicial

        def _on_progress(steps_now: List[Dict[str, Any]], out_now: str, status_now: str) -> None:
            nonlocal printed_steps_count
            while printed_steps_count < len(steps_now):
                st = steps_now[printed_steps_count]
                printed_steps_count += 1
                line = _format_step_for_cli(st)
                if line:
                    print(line, flush=True)

        res = await run_local_agent_turn(
            prompt=prompt,
            workdir=workdir,
            model_name=model_name,
            previous_interaction_id=prev_id,
            system_instruction=session.get("system_instruction", ""),
            history_turns=turns,
            api_key=api_key,
            on_progress=_on_progress,
        )
        new_id = res.get("id", f"local_{next_index}")
        env_label = f"local:{workdir}"
        out_text = res.get("output_text", "")
        new_title = None
        if not session.get("title_locked_by_user"):
            proposed = (res.get("_agent_session_title") or "").strip()
            if proposed:
                new_title = proposed
            elif is_default_session_title(session.get("title")):
                new_title = synthesize_agent_session_title(prompt, out_text, session.get("title", ""))

        upsert_turn(
            session_id=session["id"],
            interaction_id=new_id,
            turn_index=next_index,
            previous_interaction_id=prev_id,
            environment_id=env_label,
            user_input_text=prompt,
            output_text=out_text,
            status=res.get("status", "completed"),
            steps=res.get("steps", []),
            usage=res.get("usage", {}),
            raw=res,
            created_at=res.get("created"),
        )
        update_session(
            session["id"],
            title=new_title,
            execution_mode="local",
            local_workdir=workdir,
            last_interaction_id=new_id if res.get("status") in ("completed", "incomplete") else prev_id,
            environment_id=env_label,
        )
        return res
    else:
        print(f"{Colors.DIM}⏳ Enviando para o Sandbox Remoto (Google Antigravity)...{Colors.RESET}", flush=True)
        remote_prev_id = prev_id.strip()
        if remote_prev_id.startswith("local_"):
            remote_prev_id = ""

        raw_env = (session.get("environment_id") or "").strip()
        remote_env = "" if raw_env.startswith("local:") else raw_env

        history_lines: List[str] = []
        for t in turns[-6:]:
            if t.get("status") in ("completed", "incomplete"):
                u_txt = (t.get("user_input_text") or "").strip()
                o_txt = (t.get("output_text") or "").strip()
                if u_txt or o_txt:
                    history_lines.append(f"Usuário: {u_txt[:600]}\nAgente: {o_txt[:1200]}")
        fallback_history = "\n---\n".join(history_lines) if history_lines else None

        effective_prompt = prompt
        if not remote_prev_id and fallback_history:
            effective_prompt = (
                f"[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n{fallback_history}\n\n"
                f"[NOVA INSTRUÇÃO DO USUÁRIO]:\n{prompt}"
            )

        payload = build_create_payload(
            prompt=effective_prompt,
            mode="agent",
            agent_name=agent_name,
            model_name=model_name,
            previous_interaction_id=remote_prev_id or None,
            environment_id=remote_env,
            background=True,
            sources=session.get("sources", []),
            uploaded_files=session.get("uploaded_files", []),
            allow_agent_session_naming=not session.get("title_locked_by_user"),
        )
        interaction_resp = await create_interaction(
            payload,
            api_key=api_key,
            fallback_history_text=fallback_history,
        )
        inter_id = interaction_resp.get("id", "")
        status = interaction_resp.get("status", "in_progress")
        printed_count = 0

        while status == "in_progress" and inter_id:
            await asyncio.sleep(3.5)
            interaction_resp = await get_interaction(inter_id, include_input=True, api_key=api_key)
            status = interaction_resp.get("status", "completed")
            steps = interaction_resp.get("steps") or []
            while printed_count < len(steps):
                line = _format_step_for_cli(steps[printed_count])
                printed_count += 1
                if line:
                    print(line, flush=True)

        _, out_text = extract_texts_from_interaction(interaction_resp, fallback_user_input=prompt)
        ret_env = clean_environment_id(interaction_resp.get("environment_id") or remote_env)
        if ret_env.startswith("local:"):
            ret_env = ""

        new_title = None
        if not session.get("title_locked_by_user"):
            proposed = (interaction_resp.get("_agent_session_title") or "").strip()
            if proposed:
                new_title = proposed
            elif is_default_session_title(session.get("title")):
                new_title = synthesize_agent_session_title(prompt, out_text, session.get("title", ""))

        upsert_turn(
            session_id=session["id"],
            interaction_id=inter_id,
            turn_index=next_index,
            previous_interaction_id=remote_prev_id,
            environment_id=ret_env,
            user_input_text=prompt,
            output_text=out_text,
            status=status,
            steps=interaction_resp.get("steps", []) or [],
            usage=interaction_resp.get("usage", {}) or {},
            raw=interaction_resp,
            created_at=interaction_resp.get("created"),
        )
        update_session(
            session["id"],
            title=new_title,
            execution_mode="remote",
            environment_id=ret_env,
            last_interaction_id=inter_id if status in ("completed", "incomplete") else remote_prev_id,
        )
        interaction_resp["output_text"] = out_text
        return interaction_resp


def run_cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="antigravity-studio cli",
        description="CLI / Agente Local do Antigravity Studio (estilo gemini-cli / claude code).",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt opcional para execução direta.")
    parser.add_argument("-p", "--prompt", "--prompt-text", dest="prompt_flag", help="Executa um prompt único e encerra.")
    parser.add_argument("-w", "-C", "--workdir", default=get_default_local_workdir(), help="Diretório local do projeto.")
    parser.add_argument(
        "--mode",
        choices=["local", "remote"],
        default="local",
        help="Modo de execução: 'local' (Máquina Local) ou 'remote' (Sandbox Remoto Google).",
    )
    parser.add_argument(
        "--model",
        default="gemini-4-argon",
        help="Modelo Gemini a utilizar (ex: gemini-4-argon, gemini-4-carbon, gemini-3.8-flash).",
    )
    parser.add_argument("--session", default="", help="ID de sessão existente para continuar.")
    parser.add_argument("--api-key", default="", help="Chave GEMINI_API_KEY opcional.")

    args = parser.parse_args(argv)
    init_db()

    if args.api_key:
        set_setting("gemini_api_key", args.api_key.strip())

    workdir = resolve_local_path(args.workdir, get_default_local_workdir())
    os.makedirs(workdir, exist_ok=True)
    execution_mode = args.mode
    model_name = args.model

    if args.session and get_session(args.session):
        session = get_session(args.session)
        assert session is not None
    else:
        session = create_session(
            title=f"CLI Local ({os.path.basename(workdir) or 'workspace'})",
            mode="agent",
            model_name=model_name,
            execution_mode=execution_mode,
            local_workdir=workdir,
            environment_id=f"local:{workdir}" if execution_mode == "local" else "",
        )

    pos_tokens = [t for idx, t in enumerate(args.prompt or []) if not (idx == 0 and t == "cli")]
    one_shot = (args.prompt_flag or " ".join(pos_tokens)).strip()
    if one_shot:
        res = asyncio.run(
            execute_cli_prompt(
                prompt=one_shot,
                session_id=session["id"],
                execution_mode=execution_mode,
                workdir=workdir,
                model_name=model_name,
                api_key=args.api_key or None,
            )
        )
        print(f"\n{Colors.BOLD}{Colors.GREEN}🤖 Antigravity:{Colors.RESET}\n{res.get('output_text', '')}\n")
        return 0 if res.get("status") != "failed" else 1

    _print_banner(execution_mode, workdir, model_name, session["id"])

    while True:
        try:
            mode_tag = "local" if execution_mode == "local" else "remote"
            folder_name = os.path.basename(workdir.rstrip("/\\")) or workdir
            user_in = input(f"{Colors.BOLD}{Colors.BLUE}[{mode_tag}:{folder_name}] ❯ {Colors.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Colors.DIM}Encerrando Antigravity CLI.{Colors.RESET}")
            break

        if not user_in:
            continue

        if user_in.lower() in ("/exit", "/quit", "exit", "quit"):
            print(f"{Colors.DIM}Até logo! Sessão salva em SQLite: {session['id']}{Colors.RESET}")
            break

        if user_in.lower() == "/help":
            print(
                f"\n{Colors.BOLD}Comandos disponíveis no Antigravity CLI:{Colors.RESET}\n"
                "  /tools           Lista todas as Capabilities e Supported Tools ativas\n"
                "  /cd <caminho>    Altera o diretório local do projeto (local_workdir)\n"
                "  /pwd             Exibe o diretório local atual\n"
                "  /ls              Lista os arquivos da pasta local atual\n"
                "  /local           Alterna para Modo: Máquina Local\n"
                "  /remote          Alterna para Modo: Sandbox Remoto (Google)\n"
                "  /model <nome>    Altera o modelo (ex: gemini-4-argon, gemini-4-carbon)\n"
                "  /exit            Sai do CLI\n"
            )
            continue

        if user_in.lower() == "/tools":
            _print_tools_list()
            continue

        if user_in.lower() == "/pwd":
            print(f"📂 Diretório atual: {Colors.YELLOW}{workdir}{Colors.RESET}")
            continue

        if user_in.lower().startswith("/cd "):
            target_dir = user_in[4:].strip()
            resolved = resolve_local_path(target_dir, workdir)
            if os.path.isdir(resolved):
                workdir = resolved
                update_session(session["id"], local_workdir=workdir, environment_id=f"local:{workdir}" if execution_mode == "local" else None)
                print(f"✅ Diretório do projeto alterado para: {Colors.YELLOW}{workdir}{Colors.RESET}")
            else:
                print(f"{Colors.RED}❌ Diretório não encontrado: {resolved}{Colors.RESET}")
            continue

        if user_in.lower() == "/ls":
            for item in sorted(os.listdir(workdir)):
                full_p = os.path.join(workdir, item)
                prefix = "📁" if os.path.isdir(full_p) else "📄"
                print(f"  {prefix} {item}")
            continue

        if user_in.lower() == "/local":
            execution_mode = "local"
            update_session(session["id"], execution_mode="local", local_workdir=workdir, environment_id=f"local:{workdir}")
            print(f"✅ Alternado para {Colors.GREEN}Modo: Máquina Local{Colors.RESET} ({workdir})")
            continue

        if user_in.lower() == "/remote":
            execution_mode = "remote"
            cur_sess = get_session(session["id"]) or {}
            cur_last = (cur_sess.get("last_interaction_id") or "").strip()
            update_session(
                session["id"],
                execution_mode="remote",
                environment_id="",
                last_interaction_id="" if cur_last.startswith("local_") else None,
            )
            print(f"✅ Alternado para {Colors.MAGENTA}Modo: Sandbox Remoto (Google){Colors.RESET}")
            continue

        if user_in.lower().startswith("/model "):
            model_name = user_in[7:].strip() or model_name
            update_session(session["id"], model_name=model_name)
            print(f"✅ Modelo atualizado para: {Colors.BLUE}{model_name}{Colors.RESET}")
            continue

        try:
            res = asyncio.run(
                execute_cli_prompt(
                    prompt=user_in,
                    session_id=session["id"],
                    execution_mode=execution_mode,
                    workdir=workdir,
                    model_name=model_name,
                    api_key=args.api_key or None,
                )
            )
            print(f"\n{Colors.BOLD}{Colors.GREEN}🤖 Antigravity:{Colors.RESET}\n{res.get('output_text', '')}\n")
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}⚠️ Execução interrompida pelo usuário.{Colors.RESET}\n")
        except Exception as exc:
            print(f"\n{Colors.RED}❌ Erro: {exc}{Colors.RESET}\n")

    return 0
