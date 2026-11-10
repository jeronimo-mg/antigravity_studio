import asyncio
import base64
import os
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .database import (
    create_session,
    delete_session,
    delete_turn,
    get_session,
    get_setting,
    init_db,
    list_sessions,
    now_iso,
    set_setting,
    update_session,
    upsert_turn,
)
from .interactions_client import (
    build_create_payload,
    cancel_interaction,
    clean_environment_id,
    create_interaction,
    create_remote_environment,
    delete_session_from_files_api,
    download_environment_snapshot,
    extract_and_clean_session_title,
    extract_texts_from_interaction,
    format_session_as_markdown,
    get_interaction,
    get_synced_session_from_files_api,
    is_default_session_title,
    list_environment_files,
    list_synced_sessions_from_files_api,
    pack_files_as_tar_gz,
    push_session_to_files_api,
    recover_conversation_chain,
    resolve_api_key,
    synthesize_agent_session_title,
    upload_file_to_environment,
)
from .local_agent import (
    ACTIVE_LOCAL_RUNS,
    ANTIGRAVITY_SUPPORTED_TOOLS_METADATA,
    execute_local_tool_call,
    get_default_local_workdir,
    resolve_local_path,
    run_local_agent_turn,
    tool_find_by_name,
    tool_list_dir,
    tool_view_file,
    tool_write_to_file,
)

app = FastAPI(
    title="Antigravity Studio Playground",
    description="Interface visual estilo Google AI Studio com recuperação e continuação completa de sessões Antigravity via previous_interaction_id, environment_id e Agente Local (CLI / Máquina Local).",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


init_db()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class SettingsUpdate(BaseModel):
    api_key: str


class SessionCreateRequest(BaseModel):
    title: Optional[str] = "Nova Conversa Antigravity"
    title_locked_by_user: Optional[bool] = False
    mode: Optional[str] = "agent"
    execution_mode: Optional[str] = "remote"
    local_workdir: Optional[str] = ""
    agent_name: Optional[str] = "antigravity-preview-09-2026"
    model_name: Optional[str] = "gemini-4-argon"
    environment_id: Optional[str] = ""
    last_interaction_id: Optional[str] = ""
    max_total_tokens: Optional[int] = 0
    system_instruction: Optional[str] = ""
    sources: Optional[list] = None
    uploaded_files: Optional[list] = None


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    title_locked_by_user: Optional[bool] = None
    mode: Optional[str] = None
    execution_mode: Optional[str] = None
    local_workdir: Optional[str] = None
    agent_name: Optional[str] = None
    model_name: Optional[str] = None
    environment_id: Optional[str] = None
    last_interaction_id: Optional[str] = None
    max_total_tokens: Optional[int] = None
    system_instruction: Optional[str] = None
    sources: Optional[list] = None
    uploaded_files: Optional[list] = None


class ImportChainRequest(BaseModel):
    interaction_id: str
    environment_id: Optional[str] = ""
    title: Optional[str] = ""
    mode: Optional[str] = "agent"
    execution_mode: Optional[str] = "remote"
    local_workdir: Optional[str] = ""
    agent_name: Optional[str] = "antigravity-preview-09-2026"
    model_name: Optional[str] = "gemini-4-argon"
    api_key: Optional[str] = None
    fetch_history: bool = True


class ForkSessionRequest(BaseModel):
    up_to_interaction_id: str
    title: Optional[str] = None


class SendMessageRequest(BaseModel):
    prompt: str
    mode: Optional[str] = None
    execution_mode: Optional[str] = None
    local_workdir: Optional[str] = None
    agent_name: Optional[str] = None
    model_name: Optional[str] = None
    previous_interaction_id: Optional[str] = None
    environment_id: Optional[str] = None
    max_total_tokens: Optional[int] = None
    system_instruction: Optional[str] = None
    background: bool = False
    api_key: Optional[str] = None
    sources: Optional[list] = None


@app.middleware("http")
async def disable_static_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/index.html", "/app.js") or path.endswith(".js") or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/health")
@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    key = resolve_api_key()
    return {
        "status": "ok",
        "has_api_key": bool(key),
        "masked_key": f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("configurada" if key else ""),
    }


@app.get("/v1/models")
@app.get("/api/v1/models")
def list_models_compat() -> Dict[str, Any]:
    """
    Endpoint de compatibilidade para evitar 404 Not Found e resets de conexão TCP (WinError 64)
    causados por extensões ou monitores locais que sondam 127.0.0.1:8000/v1/models.
    """
    return {
        "object": "list",
        "data": [
            {"id": "antigravity-preview-09-2026", "object": "model", "owned_by": "google"},
            {"id": "gemini-4-argon", "object": "model", "owned_by": "google"},
        ],
    }


def _find_uploaded_files_for_environment(environment_id: Optional[str]) -> List[Dict[str, Any]]:
    """
    Procura no banco SQLite se alguma sessão anterior já realizou uploads para o mesmo environment_id,
    permitindo herdar automaticamente o registro de pacotes/arquivos enviados quando o usuário
    informa um environment_id existente em uma nova sessão.
    """
    clean_env = clean_environment_id(environment_id or "")
    if not clean_env or clean_env == "remote":
        return []
    merged: List[Dict[str, Any]] = []
    seen_keys = set()
    for s in list_sessions():
        s_env = clean_environment_id(s.get("environment_id") or "")
        for uf in s.get("uploaded_files") or []:
            if not isinstance(uf, dict):
                continue
            uf_env = clean_environment_id(uf.get("environment_id") or s_env)
            if uf_env == clean_env:
                key = (uf.get("target_path", ""), uf.get("label", ""), uf.get("uploaded_at", ""))
                if key not in seen_keys:
                    seen_keys.add(key)
                    merged.append(uf)
    return merged


@app.get("/api/settings")
def read_settings() -> Dict[str, Any]:
    key = resolve_api_key()
    return {
        "has_api_key": bool(key),
        "masked_key": f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("configurada" if key else ""),
    }


@app.post("/api/settings")
def update_settings(body: SettingsUpdate) -> Dict[str, Any]:
    set_setting("gemini_api_key", body.api_key.strip())
    key = resolve_api_key()
    return {
        "status": "saved",
        "has_api_key": bool(key),
        "masked_key": f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("configurada" if key else ""),
    }


@app.get("/api/sessions")
def api_list_sessions() -> Dict[str, Any]:
    return {"sessions": list_sessions()}


class LoadChainRequest(BaseModel):
    interaction_id: Optional[str] = None
    api_key: Optional[str] = None


def _resolve_next_session_title(
    session: Dict[str, Any],
    user_prompt: str = "",
    output_text: str = "",
    explicit_agent_title: Optional[str] = None,
    turn_index: int = 1,
) -> Optional[str]:
    """
    Calcula o próximo título da sessão gerenciado pelo agente:
    - Se `title_locked_by_user` for True, retorna None (respeitando o nome escolhido pelo usuário).
    - Se o agente emitiu `<session_title>...</session_title>` (`explicit_agent_title`), usa o novo título do agente
      (permitindo ao agente renomear a sessão livremente conforme ela progride).
    - Se ainda não houver tag explícita (ex: turno em andamento ou 1º turno), sintetiza um nome descritivo
      a partir do tópico do turno.
    """
    if session.get("title_locked_by_user"):
        return None

    current_title = (session.get("title") or "").strip()
    if explicit_agent_title and explicit_agent_title.strip():
        return explicit_agent_title.strip()

    tag_from_out, _ = extract_and_clean_session_title(output_text or "")
    if tag_from_out:
        return tag_from_out

    clean_prompt_low = (user_prompt or "").strip().lower()
    is_short_continuation = clean_prompt_low in (
        "continue",
        "continuar",
        "ok",
        "sim",
        "pode fazer",
        "prosseguir",
        "prossiga",
        "1",
        "2",
    ) or len(clean_prompt_low) < 6

    if is_default_session_title(current_title) or turn_index <= 1:
        return synthesize_agent_session_title(user_prompt, output_text, current_title)

    # Em turnos subsequentes (turn_index > 1), se o prompt trouxer uma nova instrução substancial
    # ou a resposta tiver um cabeçalho temático claro, atualiza o título da sessão
    if not is_short_continuation and (output_text or len(clean_prompt_low) >= 15):
        candidate = synthesize_agent_session_title(user_prompt, output_text, current_title)
        if candidate and not is_default_session_title(candidate):
            return candidate

    return current_title or "Sessão Antigravity"


async def _load_chain_into_session(
    session_id: str,
    target_id: str,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clean_target = (target_id or "").strip()
    if not clean_target:
        return get_session(session_id)

    existing = get_session(session_id)
    if not existing:
        return None

    chain = await recover_conversation_chain(clean_target, api_key=api_key)
    if not chain:
        return existing

    last_item = chain[-1]
    detected_agent = last_item.get("agent") or existing.get("agent_name") or "antigravity-preview-09-2026"
    detected_model = (
        last_item.get("model")
        or (last_item.get("agent_config") or {}).get("model")
        or existing.get("model_name")
        or "gemini-4-argon"
    )
    detected_mode = "agent" if last_item.get("agent") else (existing.get("mode") or "model")
    detected_env = clean_environment_id(
        last_item.get("environment_id")
        or (last_item.get("environment") if isinstance(last_item.get("environment"), str) else "")
        or existing.get("environment_id")
        or ""
    )

    latest_agent_title: Optional[str] = None
    last_u_text = ""
    last_out_text = ""

    for idx, item in enumerate(chain, start=1):
        u_text, out_text = extract_texts_from_interaction(item)
        if item.get("_agent_session_title"):
            latest_agent_title = str(item["_agent_session_title"])
        if u_text:
            last_u_text = u_text
        if out_text:
            last_out_text = out_text
        item_env = clean_environment_id(
            item.get("environment_id")
            or (item.get("environment") if isinstance(item.get("environment"), str) else "")
            or detected_env
        )
        upsert_turn(
            session_id=session_id,
            interaction_id=item.get("id", f"turn_{idx}"),
            turn_index=idx,
            previous_interaction_id=item.get("previous_interaction_id", ""),
            environment_id=item_env,
            user_input_text=u_text,
            output_text=out_text,
            status=item.get("status", "completed"),
            steps=item.get("steps", []) or [],
            usage=item.get("usage", {}) or {},
            raw=item,
            created_at=item.get("created") or item.get("createTime"),
        )

    current_title = (existing.get("title") or "").strip()
    if existing.get("title_locked_by_user") and not is_default_session_title(current_title):
        auto_title = current_title
    elif latest_agent_title:
        auto_title = latest_agent_title
    elif is_default_session_title(current_title):
        first_user_text, first_out_text = extract_texts_from_interaction(chain[0], "")
        auto_title = synthesize_agent_session_title(
            last_u_text or first_user_text,
            last_out_text or first_out_text,
            f"Sessão ({clean_target[:12]}...)",
        )
    else:
        auto_title = current_title

    update_kwargs: Dict[str, Any] = {
        "title": auto_title,
        "mode": detected_mode,
        "agent_name": detected_agent,
        "model_name": detected_model,
        "last_interaction_id": last_item.get("id", clean_target),
    }
    if detected_env:
        update_kwargs["environment_id"] = detected_env
        if not existing.get("uploaded_files"):
            inherited = _find_uploaded_files_for_environment(detected_env)
            if inherited:
                update_kwargs["uploaded_files"] = inherited

    return update_session(session_id, **update_kwargs)


@app.post("/api/sessions")
async def api_create_session(body: SessionCreateRequest) -> Dict[str, Any]:
    inherited_uploads = body.uploaded_files
    if not inherited_uploads and body.environment_id:
        inherited_uploads = _find_uploaded_files_for_environment(body.environment_id)
    eff_exec_mode = (body.execution_mode or "remote").strip().lower()
    if eff_exec_mode not in ("remote", "local"):
        eff_exec_mode = "remote"
    eff_workdir = (body.local_workdir or "").strip() or get_default_local_workdir()
    custom_title = (body.title or "Nova Conversa Antigravity").strip()
    locked_by_user = bool(body.title_locked_by_user) or not is_default_session_title(custom_title)
    session = create_session(
        title=custom_title,
        title_locked_by_user=locked_by_user,
        mode=body.mode or "agent",
        execution_mode=eff_exec_mode,
        local_workdir=eff_workdir,
        agent_name=body.agent_name or "antigravity-preview-09-2026",
        model_name=body.model_name or "gemini-4-argon",
        environment_id=body.environment_id or (f"local:{eff_workdir}" if eff_exec_mode == "local" else ""),
        last_interaction_id=body.last_interaction_id or "",
        max_total_tokens=body.max_total_tokens or 0,
        system_instruction=body.system_instruction or "",
        sources=body.sources or [],
        uploaded_files=inherited_uploads or [],
    )
    if body.last_interaction_id and body.last_interaction_id.strip():
        try:
            loaded = await _load_chain_into_session(session["id"], body.last_interaction_id.strip())
            if loaded:
                return loaded
        except Exception:
            pass
    return session


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: str) -> Dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return session


@app.patch("/api/sessions/{session_id}")
async def api_update_session(session_id: str, body: SessionUpdateRequest) -> Dict[str, Any]:
    existing = get_session(session_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    patch_data = body.model_dump(exclude_unset=True)
    if "title" in patch_data and patch_data["title"] is not None:
        new_title_clean = str(patch_data["title"]).strip()
        old_title_clean = str(existing.get("title") or "").strip()
        if new_title_clean and new_title_clean != old_title_clean and "title_locked_by_user" not in patch_data:
            patch_data["title_locked_by_user"] = True
    new_env = patch_data.get("environment_id")
    if new_env and not existing.get("uploaded_files") and "uploaded_files" not in patch_data:
        inherited = _find_uploaded_files_for_environment(new_env)
        if inherited:
            patch_data["uploaded_files"] = inherited
    updated = update_session(session_id, **patch_data)

    new_last_id = (patch_data.get("last_interaction_id") or "").strip()
    if new_last_id:
        existing_turn_ids = {t.get("interaction_id") for t in (existing.get("turns") or [])}
        if new_last_id not in existing_turn_ids:
            try:
                loaded = await _load_chain_into_session(session_id, new_last_id)
                if loaded:
                    updated = loaded
            except Exception:
                pass
    return updated  # type: ignore


@app.post("/api/sessions/{session_id}/load-chain")
async def api_load_chain_into_existing_session(
    session_id: str,
    body: Optional[LoadChainRequest] = None,
) -> Dict[str, Any]:
    """
    Carrega na sessão atual toda a cadeia de turnos anteriores a partir do
    previous_interaction_id (informado no body ou salvo em session.last_interaction_id).
    """
    existing = get_session(session_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    target_id = (
        (body.interaction_id if body and body.interaction_id else None)
        or existing.get("last_interaction_id")
        or ""
    ).strip()
    if not target_id:
        raise HTTPException(
            status_code=400,
            detail="Informe um previous_interaction_id válido (ex: v1_...) para carregar a conversa anterior.",
        )

    try:
        updated = await _load_chain_into_session(
            session_id=session_id,
            target_id=target_id,
            api_key=body.api_key if body else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    turns = (updated or {}).get("turns", [])
    asyncio.create_task(_bg_push_session_sync(session_id, api_key=body.api_key if body else None))
    return {
        "session": updated,
        "imported_turns": len(turns),
        "mode": "full_chain_recovered",
    }


def _merge_uploaded_files(
    local_uploads: Optional[List[Dict[str, Any]]],
    remote_uploads: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Mescla listas de arquivos enviados (uploaded_files) sem duplicar target_path."""
    merged: List[Dict[str, Any]] = []
    seen_paths = set()
    for item in list(local_uploads or []) + list(remote_uploads or []):
        if not isinstance(item, dict):
            continue
        tp = str(item.get("target_path") or "").strip()
        if not tp or tp in seen_paths:
            continue
        seen_paths.add(tp)
        merged.append(item)
    return merged


async def _bg_push_session_sync(session_id: str, api_key: Optional[str] = None) -> None:
    """Envia silenciosamente o ponteiro da sessão para a Gemini Files API em background."""
    try:
        sess = get_session(session_id)
        if sess:
            await push_session_to_files_api(sess, api_key=api_key)
    except Exception:
        pass


@app.post("/api/sessions/sync")
async def api_sync_sessions_cloud(api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Sincronização bidirecional de sessões via Gemini Files API (v1beta/files):
    1. Pull: Busca todas as sessões publicadas na Files API (AG_SYNC_V1|...),
       cria ou atualiza sessões locais, sincroniza uploaded_files e recupera os turnos
       automaticamente via last_interaction_id respeitando a trava de turn_count.
    2. Push + Auto-Renovação de TTL (48h): Publica/atualiza na Files API todas as sessões locais
       que possuem estado mais recente (ou cujo ponteiro na nuvem está próximo de expirar as 48h).
    """
    try:
        remote_items = await list_synced_sessions_from_files_api(api_key=api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    pulled_count = 0
    updated_count = 0
    pushed_count = 0
    renewed_ttl_count = 0

    remote_by_id: Dict[str, Dict[str, Any]] = {r["id"]: r for r in remote_items if r.get("id")}

    # 1. PULL: aplica sessões da nuvem localmente
    for sid, rmeta in remote_by_id.items():
        r_last_id = (rmeta.get("last_interaction_id") or "").strip()
        r_env_id = clean_environment_id(rmeta.get("environment_id") or "")
        r_title = (rmeta.get("title") or "Conversa Antigravity").strip()
        r_locked = bool(rmeta.get("title_locked_by_user"))
        r_mode = (rmeta.get("mode") or "agent").strip()
        r_agent = (rmeta.get("agent_name") or "antigravity-preview-09-2026").strip()
        r_model = (rmeta.get("model_name") or "gemini-4-argon").strip()
        r_updated = (rmeta.get("updated_at") or "").strip()
        r_uploads = rmeta.get("uploaded_files") or []

        local_sess = get_session(sid)
        if not local_sess:
            create_session(
                title=r_title,
                title_locked_by_user=r_locked,
                mode=r_mode,
                agent_name=r_agent,
                model_name=r_model,
                environment_id=r_env_id,
                last_interaction_id=r_last_id,
                uploaded_files=r_uploads,
                session_id=sid,
                updated_at=r_updated,
            )
            if r_last_id:
                try:
                    await _load_chain_into_session(sid, r_last_id, api_key=api_key)
                except Exception:
                    pass
            pulled_count += 1
        else:
            local_turns = local_sess.get("turns") or []
            existing_turn_ids = {t.get("interaction_id") for t in local_turns}
            local_last_id = (local_sess.get("last_interaction_id") or "").strip()
            local_env_id = clean_environment_id(local_sess.get("environment_id") or "")
            local_uploads = local_sess.get("uploaded_files") or []
            merged_uploads = _merge_uploaded_files(local_uploads, r_uploads)
            uploads_changed = len(merged_uploads) != len(local_uploads)
            should_update_title = not local_sess.get("title_locked_by_user") and (
                is_default_session_title(local_sess.get("title")) or r_locked
            )

            if r_last_id and ((r_last_id != local_last_id and r_last_id not in existing_turn_ids) or not local_turns):
                update_session(
                    sid,
                    title=r_title if should_update_title else None,
                    title_locked_by_user=True if r_locked else None,
                    environment_id=r_env_id or local_env_id or None,
                    last_interaction_id=r_last_id,
                    uploaded_files=merged_uploads if uploads_changed else None,
                )
                try:
                    await _load_chain_into_session(sid, r_last_id, api_key=api_key)
                except Exception:
                    pass
                updated_count += 1
            elif (r_env_id and not local_env_id) or uploads_changed:
                update_session(
                    sid,
                    environment_id=r_env_id or local_env_id or None,
                    uploaded_files=merged_uploads if uploads_changed else None,
                )
                updated_count += 1

    # 2. PUSH + AUTO-RENOVAÇÃO DE TTL (48h): envia sessões locais mais novas ou próximas de expirar
    all_local = list_sessions()
    for ls in all_local:
        sid = ls["id"]
        l_last = (ls.get("last_interaction_id") or "").strip()
        l_env = clean_environment_id(ls.get("environment_id") or "")
        l_tc = int(ls.get("turn_count") or 0)
        l_uploads = ls.get("uploaded_files") or []
        if not l_last and not l_env and not l_tc and not l_uploads:
            continue
        rmeta = remote_by_id.get(sid)
        r_tc = int((rmeta or {}).get("turn_count") or 0)
        # Trava anti-regressão: não sobrescreve a nuvem se a nuvem tiver mais turnos que o local
        if rmeta is not None and r_tc > l_tc and (rmeta.get("last_interaction_id") or "").strip():
            continue

        r_upload_paths = {
            str(u.get("target_path") or "").strip()
            for u in ((rmeta or {}).get("uploaded_files") or [])
            if isinstance(u, dict)
        }
        l_upload_paths = {
            str(u.get("target_path") or "").strip()
            for u in l_uploads
            if isinstance(u, dict) and u.get("target_path")
        }
        has_new_uploads = bool(l_upload_paths - r_upload_paths)
        needs_ttl_renewal = bool((rmeta or {}).get("_needs_ttl_renewal"))

        needs_push = (
            rmeta is None
            or (l_last and l_last != (rmeta.get("last_interaction_id") or "").strip())
            or (l_env and l_env != clean_environment_id(rmeta.get("environment_id") or ""))
            or (ls.get("title") or "").strip()[:56] != (rmeta.get("title") or "").strip()[:56]
            or l_tc > r_tc
            or has_new_uploads
            or needs_ttl_renewal
        )
        if needs_push:
            full_local = get_session(sid) or ls
            res = await push_session_to_files_api(full_local, api_key=api_key)
            if res and res.get("status") != "skipped_outdated":
                pushed_count += 1
                if needs_ttl_renewal:
                    renewed_ttl_count += 1

    final_sessions = list_sessions()
    return {
        "pulled": pulled_count,
        "updated": updated_count,
        "pushed": pushed_count,
        "renewed_ttl": renewed_ttl_count,
        "remote_total": len(remote_by_id),
        "sessions": final_sessions,
    }


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str) -> Dict[str, Any]:
    delete_session(session_id)
    asyncio.create_task(delete_session_from_files_api(session_id))
    return {"status": "deleted", "id": session_id}



@app.post("/api/sessions/import")
async def api_import_chain(body: ImportChainRequest) -> Dict[str, Any]:
    """
    Importa uma conversa existente pelo interaction_id:
    - Se fetch_history=True, percorre todos os previous_interaction_id na API
      usando include_input=true e reconstrói todos os turnos no banco SQLite.
    - Se fetch_history=False (ou se desejar apenas vincular o ID), cria a sessão
      já apontando para previous_interaction_id e environment_id para continuar.
    """
    target_id = body.interaction_id.strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="O interaction_id é obrigatório.")

    user_supplied_title = bool(body.title and body.title.strip())

    if not body.fetch_history:
        title = body.title.strip() if user_supplied_title else f"Continuação ({target_id[:14]}...)"
        session = create_session(
            title=title,
            title_locked_by_user=user_supplied_title,
            mode=body.mode or "agent",
            agent_name=body.agent_name or "antigravity-preview-09-2026",
            model_name=body.model_name or "gemini-4-argon",
            environment_id=(body.environment_id or "").strip(),
            last_interaction_id=target_id,
        )
        asyncio.create_task(_bg_push_session_sync(session["id"], api_key=body.api_key))
        return {
            "session": session,
            "imported_turns": 0,
            "mode": "linked_only",
        }

    try:
        chain = await recover_conversation_chain(target_id, api_key=body.api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not chain:
        raise HTTPException(
            status_code=404,
            detail="Nenhum turno encontrado para o interaction_id informado.",
        )

    last_item = chain[-1]
    detected_agent = last_item.get("agent") or body.agent_name or "antigravity-preview-09-2026"
    detected_model = (
        last_item.get("model")
        or (last_item.get("agent_config") or {}).get("model")
        or body.model_name
        or "gemini-4-argon"
    )
    detected_mode = "agent" if last_item.get("agent") else (body.mode or "model")
    detected_env = (
        last_item.get("environment_id")
        or (last_item.get("environment") if isinstance(last_item.get("environment"), str) else "")
        or (body.environment_id or "").strip()
    )

    first_user_text, first_out_text = extract_texts_from_interaction(chain[0], "")
    last_user_text, last_out_text = extract_texts_from_interaction(last_item, "")
    latest_agent_title = last_item.get("_agent_session_title") or chain[0].get("_agent_session_title")

    auto_title = (
        body.title.strip()
        if user_supplied_title
        else (
            str(latest_agent_title)
            if latest_agent_title
            else synthesize_agent_session_title(
                last_user_text or first_user_text,
                last_out_text or first_out_text,
                f"Importada ({target_id[:12]}...)",
            )
        )
    )

    session = create_session(
        title=auto_title,
        title_locked_by_user=user_supplied_title,
        mode=detected_mode,
        agent_name=detected_agent,
        model_name=detected_model,
        environment_id=detected_env,
        last_interaction_id=last_item.get("id", target_id),
    )

    for idx, item in enumerate(chain, start=1):
        u_text, out_text = extract_texts_from_interaction(item)
        item_env = (
            item.get("environment_id")
            or (item.get("environment") if isinstance(item.get("environment"), str) else "")
            or detected_env
        )
        upsert_turn(
            session_id=session["id"],
            interaction_id=item.get("id", f"turn_{idx}"),
            turn_index=idx,
            previous_interaction_id=item.get("previous_interaction_id", ""),
            environment_id=item_env,
            user_input_text=u_text,
            output_text=out_text,
            status=item.get("status", "completed"),
            steps=item.get("steps", []) or [],
            usage=item.get("usage", {}) or {},
            raw=item,
            created_at=item.get("created") or item.get("createTime"),
        )

    full_session = get_session(session["id"])
    asyncio.create_task(_bg_push_session_sync(session["id"], api_key=body.api_key))
    return {
        "session": full_session,
        "imported_turns": len(chain),
        "mode": "full_chain_recovered",
    }


@app.post("/api/sessions/{session_id}/fork")
def api_fork_session(session_id: str, body: ForkSessionRequest) -> Dict[str, Any]:
    """Cria uma nova sessão ramificando a conversa a partir de um turno específico."""
    source = get_session(session_id)
    if not source:
        raise HTTPException(status_code=404, detail="Sessão original não encontrada.")

    turns = source.get("turns", [])
    selected_turns = []
    target_env = source.get("environment_id", "")

    for t in turns:
        selected_turns.append(t)
        if t.get("environment_id"):
            target_env = t["environment_id"]
        if t.get("interaction_id") == body.up_to_interaction_id:
            break

    new_title = body.title or f"Fork: {source.get('title', 'Conversa')} (Turno {len(selected_turns)})"
    forked = create_session(
        title=new_title,
        mode=source.get("mode", "agent"),
        execution_mode=source.get("execution_mode", "remote"),
        local_workdir=source.get("local_workdir", ""),
        agent_name=source.get("agent_name", "antigravity-preview-09-2026"),
        model_name=source.get("model_name", "gemini-4-argon"),
        environment_id=target_env,
        last_interaction_id=body.up_to_interaction_id,
        max_total_tokens=source.get("max_total_tokens", 0),
        system_instruction=source.get("system_instruction", ""),
    )

    for idx, t in enumerate(selected_turns, start=1):
        upsert_turn(
            session_id=forked["id"],
            interaction_id=t["interaction_id"],
            turn_index=idx,
            previous_interaction_id=t.get("previous_interaction_id", ""),
            environment_id=t.get("environment_id", ""),
            user_input_text=t.get("user_input_text", ""),
            output_text=t.get("output_text", ""),
            status=t.get("status", "completed"),
            steps=t.get("steps", []),
            usage=t.get("usage", {}),
            raw=t.get("raw", {}),
            created_at=t.get("created_at"),
        )

    return get_session(forked["id"])  # type: ignore


@app.post("/api/sessions/{session_id}/messages")
async def api_send_message(session_id: str, body: SendMessageRequest) -> Dict[str, Any]:
    """
    Envia uma nova mensagem na sessão encadeando automaticamente:
    - Modo Sandbox Remoto (Google): usa `agent="antigravity-preview-09-2026"` + `environment_id` remoto na nuvem.
    - Modo Máquina Local: executa o loop do Agente Local diretamente no sistema operacional/terminal
      e pasta do projeto do usuário (`local_workdir`) mantendo todas as Capabilities e Supported Tools
      da documentação oficial (`execute_bash`, `view_file`, `write_to_file`, `replace_file_content`,
      `list_dir`, `find_by_name`, `grep_search`, `web_search`, `browse_url`).
    """
    import uuid as _uuid

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="A mensagem não pode estar vazia.")

    execution_mode = (
        body.execution_mode
        or session.get("execution_mode")
        or "remote"
    ).strip().lower()
    if execution_mode not in ("remote", "local"):
        execution_mode = "remote"

    local_workdir = resolve_local_path(
        ".",
        (body.local_workdir or session.get("local_workdir") or get_default_local_workdir()).strip(),
    )

    # PROTEÇÃO PRE-FLIGHT CONTRA DESSINCRONIZAÇÃO (SPLIT-BRAIN):
    pre_flight_synced_turns = 0
    requested_prev = (body.previous_interaction_id or "").strip()
    initial_local_last = (session.get("last_interaction_id") or "").strip()
    is_manual_fork = bool(requested_prev and initial_local_last and requested_prev != initial_local_last)

    if not is_manual_fork and execution_mode == "remote":
        try:
            cloud_meta = await get_synced_session_from_files_api(session_id, api_key=body.api_key)
            if cloud_meta:
                c_last = (cloud_meta.get("last_interaction_id") or "").strip()
                c_env = clean_environment_id(cloud_meta.get("environment_id") or "")
                c_uploads = cloud_meta.get("uploaded_files") or []
                local_turns_now = session.get("turns") or []
                existing_ids = {t.get("interaction_id") for t in local_turns_now if t.get("interaction_id")}
                local_last = (session.get("last_interaction_id") or "").strip()
                local_uploads = session.get("uploaded_files") or []
                merged_uploads = _merge_uploaded_files(local_uploads, c_uploads)
                uploads_changed = len(merged_uploads) != len(local_uploads)

                if c_last and (c_last != local_last and c_last not in existing_ids):
                    update_session(
                        session_id,
                        environment_id=c_env or session.get("environment_id") or None,
                        last_interaction_id=c_last,
                        uploaded_files=merged_uploads if uploads_changed else None,
                    )
                    loaded = await _load_chain_into_session(session_id, c_last, api_key=body.api_key)
                    if loaded:
                        session = loaded
                        pre_flight_synced_turns = max(
                            0, len(session.get("turns") or []) - len(local_turns_now)
                        )
                    else:
                        session = get_session(session_id) or session
                elif uploads_changed or (c_env and not session.get("environment_id")):
                    session = (
                        update_session(
                            session_id,
                            environment_id=c_env or session.get("environment_id") or None,
                            uploaded_files=merged_uploads if uploads_changed else None,
                        )
                        or session
                    )
        except Exception:
            pass

    mode = body.mode or session.get("mode", "agent")
    agent_name = body.agent_name or session.get("agent_name", "antigravity-preview-09-2026")
    model_name = body.model_name or session.get("model_name", "gemini-4-argon")
    prev_id = (
        requested_prev
        if is_manual_fork
        else (session.get("last_interaction_id") or requested_prev)
    )
    env_id = (
        body.environment_id
        if (body.environment_id and body.environment_id.strip())
        else session.get("environment_id", "")
    )
    max_tokens = (
        body.max_total_tokens
        if body.max_total_tokens is not None
        else session.get("max_total_tokens", 0)
    )
    sys_inst = (
        body.system_instruction
        if body.system_instruction is not None
        else session.get("system_instruction", "")
    )
    sources = (
        body.sources
        if body.sources is not None
        else session.get("sources", [])
    )
    turns = session.get("turns", [])
    next_index = len(turns) + 1
    prompt_created_at = now_iso()

    initial_agent_title = _resolve_next_session_title(
        session=session,
        user_prompt=prompt,
        output_text="",
        explicit_agent_title=None,
        turn_index=next_index,
    )
    new_title = initial_agent_title or session.get("title", "Nova Conversa Antigravity")

    # ==========================================================================
    # EXECUÇÃO NO MODO MÁQUINA LOCAL (CLI / LOCAL OS & FILESYSTEM AGENT)
    # ==========================================================================
    if execution_mode == "local":
        local_env_label = f"local:{local_workdir}"
        local_turn_id = f"local_{_uuid.uuid4().hex[:16]}"
        created_ts = prompt_created_at

        if body.background:
            initial_steps = [{"type": "user_input", "content": [{"type": "text", "text": prompt}]}]
            initial_raw = {
                "id": local_turn_id,
                "previous_interaction_id": prev_id or "",
                "environment_id": local_env_label,
                "execution_mode": "local",
                "local_workdir": local_workdir,
                "status": "in_progress",
                "steps": initial_steps,
                "output_text": "",
                "usage": {},
                "created": created_ts,
            }
            ACTIVE_LOCAL_RUNS[local_turn_id] = dict(initial_raw)
            upsert_turn(
                session_id=session_id,
                interaction_id=local_turn_id,
                turn_index=next_index,
                previous_interaction_id=prev_id or "",
                environment_id=local_env_label,
                user_input_text=prompt,
                output_text="",
                status="in_progress",
                steps=initial_steps,
                usage={},
                raw=initial_raw,
                created_at=created_ts,
            )
            update_session(
                session_id,
                title=new_title if not session.get("title_locked_by_user") else None,
                mode=mode,
                execution_mode="local",
                local_workdir=local_workdir,
                agent_name=agent_name,
                model_name=model_name,
                environment_id=local_env_label,
                last_interaction_id=local_turn_id,
                max_total_tokens=max_tokens,
                system_instruction=sys_inst,
            )

            async def _bg_local_worker() -> None:
                def _on_step(steps_now: List[Dict[str, Any]], out_now: str, status_now: str) -> None:
                    cur_state = ACTIVE_LOCAL_RUNS.get(local_turn_id) or initial_raw
                    cur_usage = cur_state.get("usage") or {}
                    upsert_turn(
                        session_id=session_id,
                        interaction_id=local_turn_id,
                        turn_index=next_index,
                        previous_interaction_id=prev_id or "",
                        environment_id=local_env_label,
                        user_input_text=prompt,
                        output_text=out_now,
                        status=status_now,
                        steps=steps_now,
                        usage=cur_usage,
                        raw=cur_state,
                        created_at=created_ts,
                    )
                    hop_agent_title = cur_state.get("_agent_session_title")
                    if hop_agent_title:
                        latest_sess = get_session(session_id)
                        if latest_sess and not latest_sess.get("title_locked_by_user"):
                            update_session(session_id, title=str(hop_agent_title))

                final_res = await run_local_agent_turn(
                    prompt=prompt,
                    workdir=local_workdir,
                    model_name=model_name,
                    previous_interaction_id=prev_id,
                    system_instruction=sys_inst,
                    history_turns=turns,
                    api_key=body.api_key,
                    interaction_id_override=local_turn_id,
                    on_progress=_on_step,
                )
                fin_status = final_res.get("status", "completed")
                fin_out = final_res.get("output_text", "")
                upsert_turn(
                    session_id=session_id,
                    interaction_id=local_turn_id,
                    turn_index=next_index,
                    previous_interaction_id=prev_id or "",
                    environment_id=local_env_label,
                    user_input_text=prompt,
                    output_text=fin_out,
                    status=fin_status,
                    steps=final_res.get("steps", []),
                    usage=final_res.get("usage", {}),
                    raw=final_res,
                    created_at=created_ts,
                )
                next_valid_last = (
                    final_res.get("_api_interaction_id") or local_turn_id
                    if fin_status not in ("failed", "cancelled")
                    else (prev_id or "")
                )
                latest_sess = get_session(session_id) or session
                resolved_fin_title = _resolve_next_session_title(
                    session=latest_sess,
                    user_prompt=prompt,
                    output_text=fin_out,
                    explicit_agent_title=final_res.get("_agent_session_title"),
                    turn_index=next_index,
                )
                update_session(
                    session_id,
                    title=resolved_fin_title,
                    execution_mode="local",
                    local_workdir=local_workdir,
                    environment_id=local_env_label,
                    last_interaction_id=next_valid_last,
                )

            asyncio.create_task(_bg_local_worker())
            return {
                "session": get_session(session_id),
                "latest_interaction": initial_raw,
                "sent_payload": {
                    "execution_mode": "local",
                    "local_workdir": local_workdir,
                    "model": model_name,
                    "input": prompt,
                },
                "pre_flight_synced_turns": 0,
            }

        # Execução síncrona no modo Máquina Local
        local_res = await run_local_agent_turn(
            prompt=prompt,
            workdir=local_workdir,
            model_name=model_name,
            previous_interaction_id=prev_id,
            system_instruction=sys_inst,
            history_turns=turns,
            api_key=body.api_key,
            interaction_id_override=local_turn_id,
        )
        fin_status = local_res.get("status", "completed")
        fin_out = local_res.get("output_text", "")
        upsert_turn(
            session_id=session_id,
            interaction_id=local_turn_id,
            turn_index=next_index,
            previous_interaction_id=prev_id or "",
            environment_id=local_env_label,
            user_input_text=prompt,
            output_text=fin_out,
            status=fin_status,
            steps=local_res.get("steps", []),
            usage=local_res.get("usage", {}),
            raw=local_res,
            created_at=local_res.get("created") or created_ts,
        )
        next_valid_last = (
            local_res.get("_api_interaction_id") or local_turn_id
            if fin_status not in ("failed", "cancelled")
            else (prev_id or "")
        )
        latest_sess = get_session(session_id) or session
        resolved_fin_title = _resolve_next_session_title(
            session=latest_sess,
            user_prompt=prompt,
            output_text=fin_out,
            explicit_agent_title=local_res.get("_agent_session_title"),
            turn_index=next_index,
        )
        update_session(
            session_id,
            title=resolved_fin_title,
            mode=mode,
            execution_mode="local",
            local_workdir=local_workdir,
            agent_name=agent_name,
            model_name=model_name,
            environment_id=local_env_label,
            last_interaction_id=next_valid_last,
            max_total_tokens=max_tokens,
            system_instruction=sys_inst,
        )
        return {
            "session": get_session(session_id),
            "latest_interaction": local_res,
            "sent_payload": {
                "execution_mode": "local",
                "local_workdir": local_workdir,
                "model": model_name,
                "input": prompt,
            },
            "pre_flight_synced_turns": 0,
        }

    # ==========================================================================
    # EXECUÇÃO NO MODO SANDBOX REMOTO (GOOGLE CLOUD CONTAINER)
    # ==========================================================================
    if env_id.startswith("local:"):
        env_id = ""

    uploaded_files = session.get("uploaded_files", []) or _find_uploaded_files_for_environment(env_id)

    if prev_id and prev_id.strip() and not prev_id.startswith("local_") and not turns:
        try:
            loaded_session = await _load_chain_into_session(session_id, prev_id.strip(), api_key=body.api_key)
            if loaded_session:
                session = loaded_session
                turns = session.get("turns", [])
                if not env_id and session.get("environment_id"):
                    env_id = session["environment_id"]
                if not uploaded_files and session.get("uploaded_files"):
                    uploaded_files = session["uploaded_files"]
        except Exception:
            pass

    if prev_id and prev_id.startswith("local_"):
        prev_id = ""

    # Monta resumo de histórico para fallback caso previous_interaction_id falhe com HTTP 500
    history_lines: List[str] = []
    for t in turns[-6:]:
        if t.get("status") in ("completed", "incomplete"):
            ut = (t.get("user_input_text") or "").strip()
            ot = (t.get("output_text") or "").strip()
            if ut or ot:
                history_lines.append(f"Usuário: {ut[:600]}\nAgente: {ot[:1200]}")
    fallback_history_text = "\n---\n".join(history_lines) if history_lines else None

    proactively_dropped_prev = False
    effective_prompt = prompt
    if prev_id and prev_id.strip() and turns:
        clean_prev = prev_id.strip()
        chain_poisoned = any(
            t.get("status") in ("failed", "cancelled")
            and (
                (t.get("previous_interaction_id") or "").strip() == clean_prev
                or t.get("interaction_id") == clean_prev
            )
            for t in turns
        ) or (turns[-1].get("status") in ("failed", "cancelled"))
        if chain_poisoned:
            prev_id = ""
            proactively_dropped_prev = True
            if fallback_history_text:
                effective_prompt = (
                    f"[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n{fallback_history_text}\n\n"
                    f"[NOVA INSTRUÇÃO DO USUÁRIO]:\n{prompt}"
                )

    allow_naming = not bool(session.get("title_locked_by_user"))
    payload = build_create_payload(
        prompt=effective_prompt,
        mode=mode,
        agent_name=agent_name,
        model_name=model_name,
        previous_interaction_id=prev_id,
        environment_id=env_id,
        max_total_tokens=max_tokens,
        system_instruction=sys_inst,
        background=body.background,
        sources=sources,
        uploaded_files=uploaded_files,
        allow_agent_session_naming=allow_naming,
    )

    try:
        interaction_resp = await create_interaction(
            payload,
            api_key=body.api_key,
            fallback_history_text=fallback_history_text,
            fallback_sources=sources,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if proactively_dropped_prev:
        interaction_resp["_dropped_previous_interaction_id"] = True

    new_interaction_id = interaction_resp.get("id", "")
    fallback_env = "" if interaction_resp.get("_dropped_environment_id") else (env_id or "")
    returned_env_id = clean_environment_id(interaction_resp.get("environment_id") or fallback_env)
    effective_prev_id = "" if interaction_resp.get("_dropped_previous_interaction_id") else (prev_id or "")
    _, out_text = extract_texts_from_interaction(interaction_resp, fallback_user_input=prompt)
    u_text = prompt
    turn_status = interaction_resp.get("status", "completed")

    next_index = len(turns) + 1
    turn_created_at = interaction_resp.get("created") or interaction_resp.get("createTime") or prompt_created_at

    upsert_turn(
        session_id=session_id,
        interaction_id=new_interaction_id,
        turn_index=next_index,
        previous_interaction_id=effective_prev_id,
        environment_id=returned_env_id,
        user_input_text=u_text,
        output_text=out_text,
        status=turn_status,
        steps=interaction_resp.get("steps", []) or [],
        usage=interaction_resp.get("usage", {}) or {},
        raw=interaction_resp,
        created_at=turn_created_at,
    )

    next_last_id = new_interaction_id if turn_status not in ("failed", "cancelled") else ""
    latest_sess = get_session(session_id) or session
    resolved_remote_title = _resolve_next_session_title(
        session=latest_sess,
        user_prompt=prompt,
        output_text=out_text,
        explicit_agent_title=interaction_resp.get("_agent_session_title"),
        turn_index=next_index,
    )

    update_session(
        session_id,
        title=resolved_remote_title,
        mode=mode,
        execution_mode="remote",
        local_workdir=local_workdir,
        agent_name=agent_name,
        model_name=model_name,
        environment_id=returned_env_id,
        last_interaction_id=next_last_id,
        max_total_tokens=max_tokens,
        system_instruction=sys_inst,
    )
    asyncio.create_task(_bg_push_session_sync(session_id, api_key=body.api_key))

    return {
        "session": get_session(session_id),
        "latest_interaction": interaction_resp,
        "sent_payload": payload,
        "pre_flight_synced_turns": pre_flight_synced_turns,
    }


@app.get("/api/sessions/{session_id}/turns/{interaction_id}/poll")
async def api_poll_turn(
    session_id: str,
    interaction_id: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Consulta o status atualizado de uma interação (tanto Sandbox Remoto quanto Máquina Local)."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    turns = session.get("turns", [])
    existing_turn = next(
        (t for t in turns if t.get("interaction_id") == interaction_id),
        None,
    )

    # Se for um turno em execução na Máquina Local (`local_...`)
    if interaction_id in ACTIVE_LOCAL_RUNS or interaction_id.startswith("local_"):
        local_state = ACTIVE_LOCAL_RUNS.get(interaction_id)
        if local_state:
            turn_index = existing_turn["turn_index"] if existing_turn else len(turns) + 1
            u_text = existing_turn.get("user_input_text", "") if existing_turn else ""
            l_out = local_state.get("output_text", "")
            l_status = local_state.get("status", "in_progress")
            upsert_turn(
                session_id=session_id,
                interaction_id=interaction_id,
                turn_index=turn_index,
                previous_interaction_id=local_state.get("previous_interaction_id", ""),
                environment_id=local_state.get("environment_id", ""),
                user_input_text=u_text,
                output_text=l_out,
                status=l_status,
                steps=local_state.get("steps", []),
                usage=local_state.get("usage", {}),
                raw=local_state,
                created_at=local_state.get("created") or (existing_turn.get("created_at") if existing_turn else None),
            )
            if not session.get("title_locked_by_user") and (l_status != "in_progress" or local_state.get("_agent_session_title")):
                polled_title = _resolve_next_session_title(
                    session=session,
                    user_prompt=u_text,
                    output_text=l_out,
                    explicit_agent_title=local_state.get("_agent_session_title"),
                    turn_index=turn_index,
                )
                if polled_title and polled_title != session.get("title"):
                    update_session(session_id, title=polled_title)
            return {
                "session": get_session(session_id),
                "latest_interaction": local_state,
            }
        if existing_turn:
            return {
                "session": session,
                "latest_interaction": existing_turn.get("raw") or {
                    "id": interaction_id,
                    "status": existing_turn.get("status", "completed"),
                    "steps": existing_turn.get("steps", []),
                    "output_text": existing_turn.get("output_text", ""),
                },
            }

    try:
        interaction_resp = await get_interaction(interaction_id, include_input=True, api_key=api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    turn_index = existing_turn["turn_index"] if existing_turn else len(turns) + 1
    fallback_input = existing_turn.get("user_input_text", "") if existing_turn else ""

    u_text, out_text = extract_texts_from_interaction(interaction_resp, fallback_user_input=fallback_input)
    if fallback_input and u_text.startswith("[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:"):
        u_text = fallback_input
    returned_env_id = interaction_resp.get("environment_id") or session.get("environment_id", "")
    prev_id_of_turn = interaction_resp.get("previous_interaction_id") or (
        existing_turn.get("previous_interaction_id", "") if existing_turn else ""
    )

    turn_status = interaction_resp.get("status", "completed")
    existing_raw = (existing_turn.get("raw") or {}) if existing_turn else {}
    was_user_cancelled = bool(existing_raw.get("_user_cancelled"))
    already_auto_retried = bool(existing_raw.get("_auto_retried_from"))

    if (
        turn_status in ("failed", "cancelled")
        and not (out_text or "").strip()
        and not was_user_cancelled
        and not already_auto_retried
        and prev_id_of_turn
        and (u_text or "").strip()
    ):
        history_lines: List[str] = []
        for t in turns:
            if t.get("interaction_id") != interaction_id and t.get("status") in ("completed", "incomplete"):
                ut = (t.get("user_input_text") or "").strip()
                ot = (t.get("output_text") or "").strip()
                if ut or ot:
                    history_lines.append(f"Usuário: {ut[:600]}\nAgente: {ot[:1200]}")
        fallback_history_text = "\n---\n".join(history_lines[-6:]) if history_lines else None
        recovery_prompt = (
            f"[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n{fallback_history_text}\n\n"
            f"[NOVA INSTRUÇÃO DO USUÁRIO]:\n{u_text}"
            if fallback_history_text
            else u_text
        )
        clean_env = clean_environment_id(returned_env_id)
        uploaded_files = session.get("uploaded_files", []) or _find_uploaded_files_for_environment(clean_env)
        recovery_payload = build_create_payload(
            prompt=recovery_prompt,
            mode=session.get("mode", "agent"),
            agent_name=session.get("agent_name", "antigravity-preview-09-2026"),
            model_name=session.get("model_name", "gemini-4-argon"),
            previous_interaction_id="",
            environment_id=clean_env,
            max_total_tokens=session.get("max_total_tokens", 0),
            system_instruction=session.get("system_instruction", ""),
            background=True,
            sources=session.get("sources", []),
            uploaded_files=uploaded_files,
            allow_agent_session_naming=not bool(session.get("title_locked_by_user")),
        )
        try:
            retry_resp = await create_interaction(
                recovery_payload,
                api_key=api_key,
                fallback_history_text=fallback_history_text,
                fallback_sources=session.get("sources", []),
            )
            new_id = retry_resp.get("id", "")
            if new_id and new_id != interaction_id:
                retry_resp["_auto_retried_from"] = interaction_id
                retry_resp["_dropped_previous_interaction_id"] = True
                _, retry_out = extract_texts_from_interaction(retry_resp, fallback_user_input=u_text)
                fallback_retry_env = "" if retry_resp.get("_dropped_environment_id") else clean_env
                retry_env = clean_environment_id(retry_resp.get("environment_id") or fallback_retry_env)
                retry_status = retry_resp.get("status", "in_progress")
                delete_turn(session_id, interaction_id)
                upsert_turn(
                    session_id=session_id,
                    interaction_id=new_id,
                    turn_index=turn_index,
                    previous_interaction_id="",
                    environment_id=retry_env,
                    user_input_text=u_text,
                    output_text=retry_out,
                    status=retry_status,
                    steps=retry_resp.get("steps", []) or [],
                    usage=retry_resp.get("usage", {}) or {},
                    raw=retry_resp,
                    created_at=retry_resp.get("created") or (existing_turn.get("created_at") if existing_turn else None),
                )
                update_session(
                    session_id,
                    environment_id=retry_env,
                    last_interaction_id=new_id if retry_status not in ("failed", "cancelled") else "",
                )
                asyncio.create_task(_bg_push_session_sync(session_id, api_key=api_key))
                return {
                    "session": get_session(session_id),
                    "latest_interaction": retry_resp,
                    "replaced_interaction_id": interaction_id,
                }
        except Exception:
            pass

    if existing_raw.get("_user_cancelled"):
        interaction_resp["_user_cancelled"] = True
    if existing_raw.get("_auto_retried_from"):
        interaction_resp["_auto_retried_from"] = existing_raw["_auto_retried_from"]

    upsert_turn(
        session_id=session_id,
        interaction_id=interaction_id,
        turn_index=turn_index,
        previous_interaction_id=prev_id_of_turn,
        environment_id=returned_env_id,
        user_input_text=u_text,
        output_text=out_text,
        status=turn_status,
        steps=interaction_resp.get("steps", []) or [],
        usage=interaction_resp.get("usage", {}) or {},
        raw=interaction_resp,
        created_at=interaction_resp.get("created") or interaction_resp.get("createTime") or (existing_turn.get("created_at") if existing_turn else None),
    )

    polled_remote_title: Optional[str] = None
    if not session.get("title_locked_by_user") and (turn_status != "in_progress" or interaction_resp.get("_agent_session_title")):
        polled_remote_title = _resolve_next_session_title(
            session=session,
            user_prompt=u_text,
            output_text=out_text,
            explicit_agent_title=interaction_resp.get("_agent_session_title"),
            turn_index=turn_index,
        )

    if turn_status in ("failed", "cancelled"):
        update_session(
            session_id,
            title=polled_remote_title,
            environment_id=clean_environment_id(returned_env_id),
            last_interaction_id="",
        )
    elif returned_env_id:
        update_session(
            session_id,
            title=polled_remote_title,
            environment_id=clean_environment_id(returned_env_id),
            last_interaction_id=interaction_id,
        )
    else:
        update_session(
            session_id,
            title=polled_remote_title,
            last_interaction_id=interaction_id,
        )

    if turn_status != "in_progress":
        asyncio.create_task(_bg_push_session_sync(session_id, api_key=api_key))

    return {
        "session": get_session(session_id),
        "latest_interaction": interaction_resp,
    }


@app.post("/api/sessions/{session_id}/turns/{interaction_id}/cancel")
async def api_cancel_turn(
    session_id: str,
    interaction_id: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    existing_turn = next(
        (t for t in session.get("turns", []) if t.get("interaction_id") == interaction_id),
        None,
    )
    turn_index = existing_turn["turn_index"] if existing_turn else len(session.get("turns", [])) + 1
    fallback_input = existing_turn.get("user_input_text", "") if existing_turn else ""

    if interaction_id in ACTIVE_LOCAL_RUNS or interaction_id.startswith("local_"):
        if interaction_id in ACTIVE_LOCAL_RUNS:
            ACTIVE_LOCAL_RUNS[interaction_id]["cancelled"] = True
            ACTIVE_LOCAL_RUNS[interaction_id]["status"] = "cancelled"
        local_state = ACTIVE_LOCAL_RUNS.get(interaction_id) or (existing_turn.get("raw") if existing_turn else {}) or {}
        local_state["status"] = "cancelled"
        local_state["_user_cancelled"] = True
        upsert_turn(
            session_id=session_id,
            interaction_id=interaction_id,
            turn_index=turn_index,
            previous_interaction_id=local_state.get("previous_interaction_id") or (existing_turn.get("previous_interaction_id", "") if existing_turn else ""),
            environment_id=local_state.get("environment_id") or (existing_turn.get("environment_id", "") if existing_turn else ""),
            user_input_text=fallback_input,
            output_text=local_state.get("output_text") or "(Execução local cancelada pelo usuário)",
            status="cancelled",
            steps=local_state.get("steps") or (existing_turn.get("steps", []) if existing_turn else []),
            usage=local_state.get("usage") or (existing_turn.get("usage", {}) if existing_turn else {}),
            raw=local_state,
            created_at=local_state.get("created") or (existing_turn.get("created_at") if existing_turn else None),
        )
        return {
            "status": "cancelled",
            "interaction": local_state,
            "session": get_session(session_id),
        }

    try:
        resp = await cancel_interaction(interaction_id, api_key=api_key)
    except Exception as exc:
        resp = {"id": interaction_id, "status": "cancelled", "_cancel_warning": str(exc)}

    latest_data = resp
    try:
        polled = await get_interaction(interaction_id, include_input=True, api_key=api_key)
        if isinstance(polled, dict):
            latest_data = polled
    except Exception:
        pass

    latest_data["_user_cancelled"] = True

    u_text, out_text = extract_texts_from_interaction(latest_data, fallback_user_input=fallback_input)
    if fallback_input and u_text.startswith("[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:"):
        u_text = fallback_input
    if not out_text and existing_turn and existing_turn.get("output_text"):
        out_text = existing_turn["output_text"]

    returned_env_id = clean_environment_id(
        latest_data.get("environment_id")
        or (existing_turn.get("environment_id", "") if existing_turn else "")
        or session.get("environment_id", "")
    )

    api_status = latest_data.get("status", "cancelled")
    final_status = "cancelled" if api_status in ("in_progress", "cancelled", "") else api_status

    upsert_turn(
        session_id=session_id,
        interaction_id=interaction_id,
        turn_index=turn_index,
        previous_interaction_id=latest_data.get("previous_interaction_id")
        or (existing_turn.get("previous_interaction_id", "") if existing_turn else ""),
        environment_id=returned_env_id,
        user_input_text=u_text,
        output_text=out_text or "(Execução cancelada pelo usuário)",
        status=final_status,
        steps=latest_data.get("steps") or (existing_turn.get("steps", []) if existing_turn else []),
        usage=latest_data.get("usage") or (existing_turn.get("usage", {}) if existing_turn else {}),
        raw=latest_data,
        created_at=latest_data.get("created") or (existing_turn.get("created_at") if existing_turn else None),
    )

    valid_prev = interaction_id if final_status == "completed" else ""

    update_session(
        session_id,
        environment_id=returned_env_id or session.get("environment_id", ""),
        last_interaction_id=valid_prev,
    )

    return {
        "status": final_status,
        "interaction": latest_data,
        "session": get_session(session_id),
    }


@app.get("/api/sessions/{session_id}/export/markdown", response_class=PlainTextResponse)
def api_export_markdown(session_id: str) -> PlainTextResponse:
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    md = format_session_as_markdown(session)
    return PlainTextResponse(
        content=md,
        headers={
            "Content-Disposition": f'attachment; filename="{session_id}.md"'
        },
    )


def _build_content_disposition(filename: str) -> str:
    import urllib.parse
    import unicodedata

    clean_name = (filename or "download").replace('"', "").replace("\r", "").replace("\n", "")
    ascii_fallback = (
        unicodedata.normalize("NFKD", clean_name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
        or "arquivo"
    )
    utf8_quoted = urllib.parse.quote(clean_name, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8''{utf8_quoted}'


def _extract_known_paths_for_env(environment_id: str, session: Optional[Dict[str, Any]] = None) -> List[str]:
    paths: List[str] = []
    uploads = []
    if session and session.get("uploaded_files"):
        uploads.extend(session.get("uploaded_files") or [])
    uploads.extend(_find_uploaded_files_for_environment(environment_id))
    for uf in uploads:
        if isinstance(uf, dict):
            tp = (uf.get("target_path") or "").strip().lstrip("/")
            if tp and tp not in paths:
                paths.append(tp)
    if session and session.get("sources"):
        for s in session.get("sources") or []:
            if isinstance(s, dict):
                st = (s.get("target") or "").strip().lstrip("/")
                if st and st not in paths:
                    paths.append(st)
    return paths


def _download_local_path_or_snapshot(base_dir: str, rel_or_abs_path: Optional[str] = None) -> Tuple[bytes, str, str]:
    import mimetypes as _mimetypes

    target = resolve_local_path(rel_or_abs_path or ".", base_dir)
    if not os.path.exists(target):
        raise ValueError(f"Caminho local não encontrado: {target}")
    if os.path.isdir(target):
        return _build_local_tar_gz(target)
    with open(target, "rb") as f:
        data = f.read()
    fname = os.path.basename(target) or "arquivo"
    mime = _mimetypes.guess_type(fname)[0] or "application/octet-stream"
    return data, fname, mime


@app.get("/api/sessions/{session_id}/environment/download")
async def api_download_session_environment(
    session_id: str,
    path: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Response:
    """Baixa o snapshot (.tar.gz) do sandbox/pasta local ou um arquivo específico associado a esta sessão."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    exec_mode = (session.get("execution_mode") or "remote").strip().lower()
    env_id = (session.get("environment_id") or "").strip()

    if exec_mode == "local" or env_id.startswith("local:"):
        base_dir = session.get("local_workdir") or env_id.removeprefix("local:") or get_default_local_workdir()
        try:
            content, filename, content_type = _download_local_path_or_snapshot(base_dir, path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return Response(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": _build_content_disposition(filename)},
        )

    if not env_id or env_id == "remote":
        raise HTTPException(
            status_code=400,
            detail="Nenhum ambiente remoto ativo nesta sessão. Envie uma mensagem primeiro para provisionar um environment_id.",
        )
    known_paths = _extract_known_paths_for_env(env_id, session=session)
    try:
        content, filename, content_type = await download_environment_snapshot(
            environment_id=env_id,
            path=path,
            known_paths=known_paths,
            api_key=api_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": _build_content_disposition(filename)
        },
    )


@app.get("/api/environments/{environment_id}/download")
async def api_download_environment_by_id(
    environment_id: str,
    path: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Response:
    """Baixa o snapshot (.tar.gz) ou um arquivo específico diretamente pelo environment_id ou pasta local."""
    clean_id = environment_id.strip()
    if clean_id.startswith("local:"):
        base_dir = clean_id.removeprefix("local:") or get_default_local_workdir()
        try:
            content, filename, content_type = _download_local_path_or_snapshot(base_dir, path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return Response(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": _build_content_disposition(filename)},
        )

    if not clean_id or clean_id == "remote":
        raise HTTPException(
            status_code=400,
            detail="Informe um environment_id válido (ex: env_... ou UUID).",
        )
    known_paths = _extract_known_paths_for_env(clean_id)
    try:
        content, filename, content_type = await download_environment_snapshot(
            environment_id=clean_id,
            path=path,
            known_paths=known_paths,
            api_key=api_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": _build_content_disposition(filename)
        },
    )


async def _parse_upload_request(request: Request) -> Dict[str, Any]:
    """
    Suporta tanto payload JSON (application/json com arquivos em Base64 ou texto)
    quanto multipart/form-data (UploadFile do navegador/cURL).
    """
    ctype = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ctype:
        form = await request.form()
        target_path = str(form.get("target_path") or form.get("path") or "/workspace").strip()
        overwrite = str(form.get("overwrite", "true")).lower() in ("true", "1", "yes", "on")
        extract = str(form.get("extract", "false")).lower() in ("true", "1", "yes", "on")
        resumable = str(form.get("resumable", "false")).lower() in ("true", "1", "yes", "on")
        pack_as_tar_gz = str(form.get("pack_as_tar_gz", "false")).lower() in ("true", "1", "yes", "on")
        auto_provision = str(form.get("auto_provision", "true")).lower() in ("true", "1", "yes", "on")
        environment_id = str(form.get("environment_id") or "").strip()
        api_key = str(form.get("api_key") or "").strip() or None
        explicit_mime = str(form.get("mime_type") or "").strip() or None

        rel_paths = form.getlist("relative_paths")
        raw_files = form.getlist("files") or form.getlist("file")
        parsed_files: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_files):
            if hasattr(item, "read"):
                f_bytes = await item.read()
                fname = getattr(item, "filename", "") or f"file_{idx}"
                rel_p = str(rel_paths[idx]) if idx < len(rel_paths) and rel_paths[idx] else fname
                f_mime = explicit_mime or getattr(item, "content_type", None) or None
                parsed_files.append({
                    "path": rel_p,
                    "bytes": f_bytes,
                    "mime_type": f_mime,
                })

        return {
            "target_path": target_path,
            "overwrite": overwrite,
            "extract": extract,
            "resumable": resumable,
            "pack_as_tar_gz": pack_as_tar_gz,
            "auto_provision": auto_provision,
            "environment_id": environment_id,
            "api_key": api_key,
            "files": parsed_files,
        }

    body = await request.json()
    target_path = str(body.get("target_path") or body.get("path") or "/workspace").strip()
    overwrite = bool(body.get("overwrite", True))
    extract = bool(body.get("extract", False))
    resumable = bool(body.get("resumable", False))
    pack_as_tar_gz = bool(body.get("pack_as_tar_gz", False))
    auto_provision = bool(body.get("auto_provision", True))
    environment_id = str(body.get("environment_id") or "").strip()
    api_key = body.get("api_key")

    parsed_files = []
    for idx, f in enumerate(body.get("files") or []):
        if not isinstance(f, dict):
            continue
        rel_p = str(f.get("path") or f.get("name") or f"file_{idx}").strip()
        b64_raw = f.get("content_base64") if f.get("content_base64") is not None else f.get("content_b64")
        if b64_raw is not None:
            b64_str = str(b64_raw)
            if "," in b64_str and b64_str.startswith("data:"):
                b64_str = b64_str.split(",", 1)[1]
            f_bytes = base64.b64decode(b64_str)
        elif "content_text" in f and f["content_text"] is not None:
            f_bytes = str(f["content_text"]).encode("utf-8")
        else:
            f_bytes = b""
        parsed_files.append({
            "path": rel_p,
            "bytes": f_bytes,
            "mime_type": f.get("mime_type") or f.get("content_type") or body.get("mime_type"),
        })

    top_b64 = body.get("content_base64") if body.get("content_base64") is not None else body.get("content_b64")
    if not parsed_files and (top_b64 is not None or "content_text" in body):
        if top_b64 is not None:
            b64_str = str(top_b64)
            if "," in b64_str and b64_str.startswith("data:"):
                b64_str = b64_str.split(",", 1)[1]
            f_bytes = base64.b64decode(b64_str)
        else:
            f_bytes = str(body.get("content_text") or "").encode("utf-8")
        parsed_files.append({
            "path": os.path.basename(target_path.rstrip("/")) or "arquivo.txt",
            "bytes": f_bytes,
            "mime_type": body.get("mime_type") or body.get("content_type"),
        })

    return {
        "target_path": target_path,
        "overwrite": overwrite,
        "extract": extract,
        "resumable": resumable,
        "pack_as_tar_gz": pack_as_tar_gz,
        "auto_provision": auto_provision,
        "environment_id": environment_id,
        "api_key": api_key,
        "files": parsed_files,
    }


@app.post("/api/sessions/{session_id}/environment/provision")
async def api_provision_session_environment(
    session_id: str,
    request: Request,
) -> Dict[str, Any]:
    """
    Provisiona proativamente um ambiente remoto (POST /v1beta/environments) para a sessão,
    permitindo obter um environment_id antes mesmo do primeiro turno (por exemplo, para
    enviar arquivos via Environments API antes do Turno #1).
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    try:
        body = await request.json()
    except Exception:
        body = {}

    api_key = body.get("api_key") if isinstance(body, dict) else None
    from_env = body.get("from_environment") if isinstance(body, dict) else None
    sources = (
        body.get("sources")
        if isinstance(body, dict) and body.get("sources") is not None
        else session.get("sources", [])
    )

    try:
        env_data = await create_remote_environment(
            sources=sources,
            from_environment=from_env,
            api_key=api_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    new_env_id = env_data.get("id") or ""
    if not new_env_id:
        raise HTTPException(
            status_code=400,
            detail="A Environments API não retornou um environment_id válido.",
        )

    updated_session = update_session(session_id, environment_id=new_env_id)
    asyncio.create_task(_bg_push_session_sync(session_id, api_key=api_key))
    return {
        "environment": env_data,
        "environment_id": new_env_id,
        "session": updated_session,
    }


@app.post("/api/sessions/{session_id}/environment/upload")
async def api_upload_to_session_environment(
    session_id: str,
    request: Request,
) -> Dict[str, Any]:
    """
    Faz upload direto de um arquivo único, múltiplos arquivos/pasta local (compactados em memória
    como .tar.gz com extract=true) ou um arquivo .tar/.tar.gz diretamente para o ambiente sandbox
    da sessão usando a Environments API:
      PUT https://generativelanguage.googleapis.com/upload/v1beta/environments/{environment_id}/files/{path}
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    try:
        parsed = await _parse_upload_request(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro ao ler dados de upload: {exc}")

    files_list: List[Dict[str, Any]] = parsed["files"]
    if not files_list:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado para upload.")

    exec_mode = (session.get("execution_mode") or "remote").strip().lower()
    raw_env_id = (parsed["environment_id"] or session.get("environment_id") or "").strip()

    if exec_mode == "local" or raw_env_id.startswith("local:"):
        base_dir = resolve_local_path(
            ".",
            session.get("local_workdir") or raw_env_id.removeprefix("local:") or get_default_local_workdir(),
        )
        os.makedirs(base_dir, exist_ok=True)
        rel_target = (parsed["target_path"] or "").strip()
        if rel_target in ("workspace", "/workspace", ".", "/"):
            rel_target = ""

        total_bytes = 0
        saved_paths: List[str] = []
        for item in files_list:
            rel_item = (item["path"] or "file.bin").lstrip("/\\")
            if len(files_list) == 1 and rel_target and not rel_target.endswith("/"):
                dest_full = resolve_local_path(rel_target, base_dir)
            elif rel_target:
                dest_full = resolve_local_path(os.path.join(rel_target, rel_item), base_dir)
            else:
                dest_full = resolve_local_path(rel_item, base_dir)
            os.makedirs(os.path.dirname(dest_full) or base_dir, exist_ok=True)
            with open(dest_full, "wb") as fp:
                fp.write(item["bytes"])
            total_bytes += len(item["bytes"])
            saved_paths.append(dest_full)

        local_env_id = f"local:{base_dir}"
        label = (
            files_list[0]["path"]
            if len(files_list) == 1
            else f"Pasta/Pacote Local ({len(files_list)} arquivos)"
        )
        current_uploaded = list(session.get("uploaded_files") or [])
        upload_record = {
            "label": label,
            "summary": label,
            "target_path": saved_paths[0] if len(saved_paths) == 1 else base_dir,
            "size_bytes": total_bytes,
            "mime_type": files_list[0].get("mime_type", "application/octet-stream"),
            "extract": False,
            "overwrite": parsed["overwrite"],
            "resumable": False,
            "upload_mode": "local_disk",
            "file_count": len(files_list),
            "environment_id": local_env_id,
            "uploaded_at": now_iso(),
        }
        current_uploaded.append(upload_record)
        updated_session = update_session(
            session_id,
            execution_mode="local",
            local_workdir=base_dir,
            environment_id=local_env_id,
            uploaded_files=current_uploaded,
        )
        return {
            "status": "uploaded",
            "provisioned_new_environment": False,
            "auto_provisioned_environment": False,
            "environment_id": local_env_id,
            "upload": upload_record,
            "upload_result": upload_record,
            "session": updated_session,
        }

    env_id = clean_environment_id(raw_env_id)
    provisioned_now = False

    # Se ainda não houver um environment_id provisionado na sessão e auto_provision=True,
    # cria o ambiente via POST /v1beta/environments antes de subir os arquivos.
    if (not env_id or env_id == "remote") and parsed["auto_provision"]:
        try:
            env_resp = await create_remote_environment(
                sources=session.get("sources", []),
                api_key=parsed["api_key"],
            )
            env_id = clean_environment_id(env_resp.get("id") or "")
            if env_id:
                provisioned_now = True
                update_session(session_id, environment_id=env_id)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Esta sessão ainda não possui um environment_id ativo e a criação automática "
                    f"via POST /v1beta/environments falhou: {exc}. "
                    f"Dica: Envie um primeiro prompt no modo Agente para provisionar o ambiente ou informe um environment_id existente."
                ),
            )

    if not env_id or env_id == "remote":
        raise HTTPException(
            status_code=400,
            detail="Informe um environment_id ativo (ex: env_...) ou habilite o provisionamento automático do ambiente.",
        )

    target_path = parsed["target_path"]
    overwrite = parsed["overwrite"]
    extract = parsed["extract"]
    resumable = parsed["resumable"]
    pack_as_tar_gz = parsed["pack_as_tar_gz"] or len(files_list) > 1

    async def _do_upload(target_env_id: str) -> Tuple[Dict[str, Any], str]:
        if pack_as_tar_gz:
            tar_tuples = [(f["path"], f["bytes"]) for f in files_list]
            archive_bytes = pack_files_as_tar_gz(tar_tuples)
            res = await upload_file_to_environment(
                environment_id=target_env_id,
                target_path=target_path,
                file_bytes=archive_bytes,
                mime_type="application/x-tar",
                overwrite=overwrite,
                extract=True,
                resumable=resumable,
                api_key=parsed["api_key"],
            )
            label = (
                f"Pasta/Pacote ({len(files_list)} arquivo{'s' if len(files_list) > 1 else ''})"
            )
            return res, label
        else:
            single = files_list[0]
            res = await upload_file_to_environment(
                environment_id=target_env_id,
                target_path=target_path,
                file_bytes=single["bytes"],
                mime_type=single.get("mime_type"),
                overwrite=overwrite,
                extract=extract,
                resumable=resumable,
                api_key=parsed["api_key"],
            )
            return res, single["path"]

    try:
        upload_res, upload_summary_label = await _do_upload(env_id)
    except Exception as exc:
        err_str = str(exc)
        # Se o environment_id antigo foi desalocado na nuvem (HTTP 404) e auto_provision está ativo,
        # provisiona um novo ambiente remoto automaticamente e refaz o upload!
        if ("HTTP 404" in err_str or "not_found" in err_str) and parsed["auto_provision"] and not provisioned_now:
            try:
                env_resp = await create_remote_environment(
                    sources=session.get("sources", []),
                    api_key=parsed["api_key"],
                )
                new_env_id = clean_environment_id(env_resp.get("id") or "")
                if new_env_id:
                    env_id = new_env_id
                    provisioned_now = True
                    update_session(session_id, environment_id=env_id, last_interaction_id="")
                    upload_res, upload_summary_label = await _do_upload(env_id)
                else:
                    raise exc
            except Exception as retry_exc:
                raise HTTPException(status_code=400, detail=str(retry_exc))
        else:
            raise HTTPException(status_code=400, detail=err_str)

    # Registra no histórico de arquivos enviados da sessão (SQLite)
    current_uploaded = list(session.get("uploaded_files") or [])
    upload_record = {
        "label": upload_summary_label,
        "summary": upload_summary_label,
        "target_path": upload_res.get("target_path") or target_path,
        "size_bytes": upload_res.get("size_bytes", 0),
        "mime_type": upload_res.get("mime_type", "application/octet-stream"),
        "extract": bool(upload_res.get("extract")),
        "overwrite": overwrite,
        "resumable": resumable,
        "upload_mode": upload_res.get("upload_mode", "direct_put"),
        "file_count": len(files_list),
        "environment_id": env_id,
        "uploaded_at": now_iso(),
    }
    current_uploaded.append(upload_record)

    updated_session = update_session(
        session_id,
        environment_id=env_id,
        uploaded_files=current_uploaded,
    )
    asyncio.create_task(_bg_push_session_sync(session_id, api_key=parsed["api_key"]))

    return {
        "status": "uploaded",
        "provisioned_new_environment": provisioned_now,
        "auto_provisioned_environment": provisioned_now,
        "environment_id": env_id,
        "upload": upload_record,
        "upload_result": upload_res,
        "session": updated_session,
    }


@app.post("/api/environments/{environment_id}/upload")
async def api_upload_to_environment_by_id(
    environment_id: str,
    request: Request,
) -> Dict[str, Any]:
    """Faz upload direto para qualquer environment_id via Environments API."""
    clean_id = clean_environment_id(environment_id)
    if not clean_id or clean_id == "remote":
        raise HTTPException(status_code=400, detail="Informe um environment_id válido (ex: env_...).")

    try:
        parsed = await _parse_upload_request(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro ao ler dados de upload: {exc}")

    files_list: List[Dict[str, Any]] = parsed["files"]
    if not files_list:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado para upload.")

    pack_as_tar_gz = parsed["pack_as_tar_gz"] or len(files_list) > 1
    try:
        if pack_as_tar_gz:
            tar_tuples = [(f["path"], f["bytes"]) for f in files_list]
            archive_bytes = pack_files_as_tar_gz(tar_tuples)
            upload_res = await upload_file_to_environment(
                environment_id=clean_id,
                target_path=parsed["target_path"],
                file_bytes=archive_bytes,
                mime_type="application/x-tar",
                overwrite=parsed["overwrite"],
                extract=True,
                resumable=parsed["resumable"],
                api_key=parsed["api_key"],
            )
        else:
            single = files_list[0]
            upload_res = await upload_file_to_environment(
                environment_id=clean_id,
                target_path=parsed["target_path"],
                file_bytes=single["bytes"],
                mime_type=single.get("mime_type"),
                overwrite=parsed["overwrite"],
                extract=parsed["extract"],
                resumable=parsed["resumable"],
                api_key=parsed["api_key"],
            )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "status": "uploaded",
        "environment_id": clean_id,
        "upload": upload_res,
        "upload_result": upload_res,
    }


def _build_local_tar_gz(target_dir: str) -> Tuple[bytes, str, str]:
    import io
    import tarfile

    buf = io.BytesIO()
    ignore_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    base_name = os.path.basename(target_dir.rstrip("/\\")) or "local_workspace"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for r, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for f in files:
                full_p = os.path.join(r, f)
                rel_p = os.path.relpath(full_p, target_dir).replace("\\", "/")
                try:
                    tar.add(full_p, arcname=f"{base_name}/{rel_p}", recursive=False)
                except Exception:
                    pass
    buf.seek(0)
    return buf.read(), f"{base_name}.tar.gz", "application/gzip"


@app.get("/api/local/info")
def api_get_local_info() -> Dict[str, Any]:
    """Retorna informações da máquina local, pasta padrão e Capabilities / Supported Tools."""
    import platform as _platform
    import sys as _sys_mod

    return {
        "os": f"{_platform.system()} {_platform.release()} ({_platform.machine()})",
        "platform": _sys_mod.platform,
        "python_version": _sys_mod.version.split()[0],
        "default_workdir": get_default_local_workdir(),
        "supported_tools": ANTIGRAVITY_SUPPORTED_TOOLS_METADATA,
    }


@app.get("/api/local/files")
def api_list_local_files(
    path: Optional[str] = None,
    workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Lista arquivos e diretórios diretamente do disco da máquina local."""
    base_dir = (workdir or "").strip() or get_default_local_workdir()
    target = resolve_local_path(path or ".", base_dir)
    res = tool_list_dir(target, default_workdir=base_dir)
    if res.get("error"):
        raise HTTPException(status_code=400, detail=str(res["error"]))
    return {
        "workdir": base_dir,
        "resolved_path": res.get("directory", target),
        "files": res.get("entries", []),
    }


class LocalToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = {}
    workdir: Optional[str] = None


@app.post("/api/local/tool")
async def api_execute_local_tool(body: LocalToolRequest) -> Dict[str, Any]:
    """Permite executar diretamente qualquer Supported Tool na máquina local."""
    base_dir = resolve_local_path(".", (body.workdir or "").strip() or get_default_local_workdir())
    res, is_err = await execute_local_tool_call(
        tool_name=body.tool_name,
        arguments=body.arguments,
        workdir=base_dir,
    )
    return {
        "tool_name": body.tool_name,
        "workdir": base_dir,
        "is_error": is_err,
        "result": res,
    }


@app.get("/api/sessions/{session_id}/environment/files")
async def api_list_session_environment_files(
    session_id: str,
    path: str = "workspace",
    recursive: bool = True,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Lista os arquivos presentes no ambiente (Sandbox Remoto ou Máquina Local) associado à sessão."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    exec_mode = (session.get("execution_mode") or "remote").strip().lower()
    raw_env = (session.get("environment_id") or "").strip()

    if exec_mode == "local" or raw_env.startswith("local:"):
        base_dir = session.get("local_workdir") or raw_env.removeprefix("local:") or get_default_local_workdir()
        req_path = "." if path in ("workspace", "", "/") else path
        target = resolve_local_path(req_path, base_dir)
        res = tool_list_dir(target, default_workdir=base_dir)
        if res.get("error"):
            raise HTTPException(status_code=400, detail=str(res["error"]))
        return {
            "environment_id": f"local:{base_dir}",
            "execution_mode": "local",
            "data": {
                "resolved_path": res.get("directory", target),
                "files": res.get("entries", []),
            },
        }

    env_id = clean_environment_id(raw_env)
    if not env_id or env_id == "remote":
        raise HTTPException(
            status_code=400,
            detail="Nenhum environment_id ativo nesta sessão ainda.",
        )
    known_paths = _extract_known_paths_for_env(env_id, session=session)
    try:
        data = await list_environment_files(
            environment_id=env_id,
            path=path,
            recursive=recursive,
            known_paths=known_paths,
            api_key=api_key,
        )
        return {"environment_id": env_id, "execution_mode": "remote", "data": data}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/environments/{environment_id}/files")
async def api_list_environment_files_by_id(
    environment_id: str,
    path: str = "workspace",
    recursive: bool = True,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Lista os arquivos presentes em um environment_id específico via Environments API ou Máquina Local."""
    if environment_id.startswith("local:"):
        base_dir = environment_id.removeprefix("local:") or get_default_local_workdir()
        req_path = "." if path in ("workspace", "", "/") else path
        target = resolve_local_path(req_path, base_dir)
        res = tool_list_dir(target, default_workdir=base_dir)
        if res.get("error"):
            raise HTTPException(status_code=400, detail=str(res["error"]))
        return {
            "environment_id": environment_id,
            "execution_mode": "local",
            "data": {
                "resolved_path": res.get("directory", target),
                "files": res.get("entries", []),
            },
        }

    clean_id = clean_environment_id(environment_id)
    known_paths = _extract_known_paths_for_env(clean_id)
    try:
        data = await list_environment_files(
            environment_id=clean_id,
            path=path,
            recursive=recursive,
            known_paths=known_paths,
            api_key=api_key,
        )
        return {"environment_id": clean_id, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


import sys as _sys

_BUNDLE_DIR = getattr(_sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC_DIR = os.path.join(_BUNDLE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

