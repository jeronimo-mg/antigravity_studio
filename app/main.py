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
    download_environment_snapshot,
    extract_texts_from_interaction,
    format_session_as_markdown,
    get_interaction,
    list_environment_files,
    pack_files_as_tar_gz,
    recover_conversation_chain,
    resolve_api_key,
    upload_file_to_environment,
)

app = FastAPI(
    title="Antigravity Studio Playground",
    description="Interface visual estilo Google AI Studio com recuperação e continuação completa de sessões Antigravity via previous_interaction_id e environment_id.",
    version="1.0.0",
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
    mode: Optional[str] = "agent"
    agent_name: Optional[str] = "antigravity-preview-09-2026"
    model_name: Optional[str] = "gemini-3.8-flash"
    environment_id: Optional[str] = ""
    last_interaction_id: Optional[str] = ""
    max_total_tokens: Optional[int] = 0
    system_instruction: Optional[str] = ""
    sources: Optional[list] = None
    uploaded_files: Optional[list] = None


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    mode: Optional[str] = None
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
    agent_name: Optional[str] = "antigravity-preview-09-2026"
    model_name: Optional[str] = "gemini-3.8-flash"
    api_key: Optional[str] = None
    fetch_history: bool = True


class ForkSessionRequest(BaseModel):
    up_to_interaction_id: str
    title: Optional[str] = None


class SendMessageRequest(BaseModel):
    prompt: str
    mode: Optional[str] = None
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
            {"id": "gemini-3.8-flash", "object": "model", "owned_by": "google"},
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
        or "gemini-3.8-flash"
    )
    detected_mode = "agent" if last_item.get("agent") else (existing.get("mode") or "model")
    detected_env = clean_environment_id(
        last_item.get("environment_id")
        or (last_item.get("environment") if isinstance(last_item.get("environment"), str) else "")
        or existing.get("environment_id")
        or ""
    )

    first_user_text, _ = extract_texts_from_interaction(chain[0], "")
    current_title = (existing.get("title") or "").strip()
    if not current_title or current_title in ("Nova Conversa Antigravity", "Nova Conversa", "Conversa Antigravity"):
        auto_title = (
            first_user_text[:48] + "..."
            if len(first_user_text) > 48
            else (first_user_text or f"Conversa ({clean_target[:12]}...)")
        )
    else:
        auto_title = current_title

    for idx, item in enumerate(chain, start=1):
        u_text, out_text = extract_texts_from_interaction(item)
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
            created_at=item.get("created"),
        )

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
    session = create_session(
        title=body.title or "Nova Conversa Antigravity",
        mode=body.mode or "agent",
        agent_name=body.agent_name or "antigravity-preview-09-2026",
        model_name=body.model_name or "gemini-3.8-flash",
        environment_id=body.environment_id or "",
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
    return {
        "session": updated,
        "imported_turns": len(turns),
        "mode": "full_chain_recovered",
    }


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str) -> Dict[str, Any]:
    delete_session(session_id)
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

    if not body.fetch_history:
        title = body.title.strip() if body.title else f"Continuação ({target_id[:14]}...)"
        session = create_session(
            title=title,
            mode=body.mode or "agent",
            agent_name=body.agent_name or "antigravity-preview-09-2026",
            model_name=body.model_name or "gemini-3.8-flash",
            environment_id=(body.environment_id or "").strip(),
            last_interaction_id=target_id,
        )
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
        or "gemini-3.8-flash"
    )
    detected_mode = "agent" if last_item.get("agent") else (body.mode or "model")
    detected_env = (
        last_item.get("environment_id")
        or (last_item.get("environment") if isinstance(last_item.get("environment"), str) else "")
        or (body.environment_id or "").strip()
    )

    first_user_text, _ = extract_texts_from_interaction(chain[0], "")
    auto_title = (
        body.title.strip()
        if body.title and body.title.strip()
        else (first_user_text[:48] + "..." if len(first_user_text) > 48 else (first_user_text or f"Importada ({target_id[:12]}...)"))
    )

    session = create_session(
        title=auto_title,
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
            created_at=item.get("created"),
        )

    full_session = get_session(session["id"])
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
        agent_name=source.get("agent_name", "antigravity-preview-09-2026"),
        model_name=source.get("model_name", "gemini-3.8-flash"),
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
    - previous_interaction_id (da sessão ou sobrescrito manualmente)
    - environment (environment_id da sessão para manter o mesmo container Linux do Antigravity)
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="A mensagem não pode estar vazia.")

    mode = body.mode or session.get("mode", "agent")
    agent_name = body.agent_name or session.get("agent_name", "antigravity-preview-09-2026")
    model_name = body.model_name or session.get("model_name", "gemini-3.8-flash")
    prev_id = (
        body.previous_interaction_id
        if body.previous_interaction_id is not None
        else session.get("last_interaction_id", "")
    )
    env_id = (
        body.environment_id
        if body.environment_id is not None
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

    uploaded_files = session.get("uploaded_files", []) or _find_uploaded_files_for_environment(env_id)
    turns = session.get("turns", [])

    if prev_id and prev_id.strip() and not turns:
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

    # Monta resumo de histórico para fallback caso previous_interaction_id falhe com HTTP 500
    history_lines: List[str] = []
    for t in turns[-6:]:
        if t.get("status") in ("completed", "incomplete"):
            ut = (t.get("user_input_text") or "").strip()
            ot = (t.get("output_text") or "").strip()
            if ut or ot:
                history_lines.append(f"Usuário: {ut[:600]}\nAgente: {ot[:1200]}")
    fallback_history_text = "\n---\n".join(history_lines) if history_lines else None


    payload = build_create_payload(
        prompt=prompt,
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
    )

    try:
        interaction_resp = await create_interaction(
            payload,
            api_key=body.api_key,
            fallback_history_text=fallback_history_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    new_interaction_id = interaction_resp.get("id", "")
    returned_env_id = clean_environment_id(interaction_resp.get("environment_id") or env_id or "")
    effective_prev_id = "" if interaction_resp.get("_dropped_previous_interaction_id") else (prev_id or "")
    _, out_text = extract_texts_from_interaction(interaction_resp, fallback_user_input=prompt)
    u_text = prompt
    turn_status = interaction_resp.get("status", "completed")

    next_index = len(turns) + 1

    # Atualiza título automático se for o primeiro turno de uma sessão com título padrão
    new_title = session.get("title", "Nova Conversa Antigravity")
    if next_index == 1 and new_title in ("Nova Conversa Antigravity", "Nova Conversa"):
        new_title = prompt[:48] + ("..." if len(prompt) > 48 else "")

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
        created_at=interaction_resp.get("created"),
    )

    next_last_id = new_interaction_id if turn_status not in ("failed", "cancelled") else (prev_id or "")

    update_session(
        session_id,
        title=new_title,
        mode=mode,
        agent_name=agent_name,
        model_name=model_name,
        environment_id=returned_env_id,
        last_interaction_id=next_last_id,
        max_total_tokens=max_tokens,
        system_instruction=sys_inst,
    )

    return {
        "session": get_session(session_id),
        "latest_interaction": interaction_resp,
        "sent_payload": payload,
    }


@app.get("/api/sessions/{session_id}/turns/{interaction_id}/poll")
async def api_poll_turn(
    session_id: str,
    interaction_id: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Consulta o status atualizado de uma interação (útil para background=True)."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")

    try:
        interaction_resp = await get_interaction(interaction_id, include_input=True, api_key=api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    existing_turn = next(
        (t for t in session.get("turns", []) if t.get("interaction_id") == interaction_id),
        None,
    )
    turn_index = existing_turn["turn_index"] if existing_turn else len(session.get("turns", [])) + 1
    fallback_input = existing_turn.get("user_input_text", "") if existing_turn else ""

    u_text, out_text = extract_texts_from_interaction(interaction_resp, fallback_user_input=fallback_input)
    returned_env_id = interaction_resp.get("environment_id") or session.get("environment_id", "")

    upsert_turn(
        session_id=session_id,
        interaction_id=interaction_id,
        turn_index=turn_index,
        previous_interaction_id=interaction_resp.get("previous_interaction_id") or (existing_turn.get("previous_interaction_id", "") if existing_turn else ""),
        environment_id=returned_env_id,
        user_input_text=u_text,
        output_text=out_text,
        status=interaction_resp.get("status", "completed"),
        steps=interaction_resp.get("steps", []) or [],
        usage=interaction_resp.get("usage", {}) or {},
        raw=interaction_resp,
        created_at=interaction_resp.get("created"),
    )

    turn_status = interaction_resp.get("status", "completed")
    if turn_status in ("failed", "cancelled"):
        # Se a execução em background falhou ou foi cancelada, restaura o último ID válido da sessão
        valid_prev = ""
        for t in reversed(session.get("turns", [])):
            if t.get("interaction_id") != interaction_id and t.get("status") in ("completed", "incomplete"):
                valid_prev = t.get("interaction_id", "")
                break
        update_session(session_id, environment_id=clean_environment_id(returned_env_id), last_interaction_id=valid_prev)
    elif returned_env_id:
        update_session(session_id, environment_id=clean_environment_id(returned_env_id), last_interaction_id=interaction_id)

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

    try:
        resp = await cancel_interaction(interaction_id, api_key=api_key)
    except Exception as exc:
        resp = {"id": interaction_id, "status": "cancelled", "_cancel_warning": str(exc)}

    # Tenta buscar os steps/textos parciais mais recentes da API
    latest_data = resp
    try:
        polled = await get_interaction(interaction_id, include_input=True, api_key=api_key)
        if isinstance(polled, dict):
            latest_data = polled
    except Exception:
        pass

    existing_turn = next(
        (t for t in session.get("turns", []) if t.get("interaction_id") == interaction_id),
        None,
    )
    turn_index = existing_turn["turn_index"] if existing_turn else len(session.get("turns", [])) + 1
    fallback_input = existing_turn.get("user_input_text", "") if existing_turn else ""

    u_text, out_text = extract_texts_from_interaction(latest_data, fallback_user_input=fallback_input)
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

    # Restaura o último interaction_id válido da sessão para que o próximo turno não aponte para o turno cancelado
    valid_prev = ""
    if final_status == "completed":
        valid_prev = interaction_id
    else:
        for t in reversed(session.get("turns", [])):
            if t.get("interaction_id") != interaction_id and t.get("status") in ("completed", "incomplete"):
                valid_prev = t.get("interaction_id", "")
                break

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


@app.get("/api/sessions/{session_id}/environment/download")
async def api_download_session_environment(
    session_id: str,
    path: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Response:
    """Baixa o snapshot (.tar.gz) do sandbox ou um arquivo específico associado a esta sessão."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    env_id = (session.get("environment_id") or "").strip()
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
    """Baixa o snapshot (.tar.gz) ou um arquivo específico diretamente pelo environment_id."""
    clean_id = environment_id.strip()
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

    env_id = clean_environment_id(parsed["environment_id"] or session.get("environment_id") or "")
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

    try:
        if pack_as_tar_gz:
            tar_tuples = [(f["path"], f["bytes"]) for f in files_list]
            archive_bytes = pack_files_as_tar_gz(tar_tuples)
            upload_res = await upload_file_to_environment(
                environment_id=env_id,
                target_path=target_path,
                file_bytes=archive_bytes,
                mime_type="application/x-tar",
                overwrite=overwrite,
                extract=True,
                resumable=resumable,
                api_key=parsed["api_key"],
            )
            upload_summary_label = (
                f"Pasta/Pacote ({len(files_list)} arquivo{'s' if len(files_list) > 1 else ''})"
            )
        else:
            single = files_list[0]
            upload_res = await upload_file_to_environment(
                environment_id=env_id,
                target_path=target_path,
                file_bytes=single["bytes"],
                mime_type=single.get("mime_type"),
                overwrite=overwrite,
                extract=extract,
                resumable=resumable,
                api_key=parsed["api_key"],
            )
            upload_summary_label = single["path"]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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


@app.get("/api/sessions/{session_id}/environment/files")
async def api_list_session_environment_files(
    session_id: str,
    path: str = "workspace",
    recursive: bool = True,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Lista os arquivos presentes no ambiente sandbox associado à sessão."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    env_id = clean_environment_id(session.get("environment_id") or "")
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
        return {"environment_id": env_id, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/environments/{environment_id}/files")
async def api_list_environment_files_by_id(
    environment_id: str,
    path: str = "workspace",
    recursive: bool = True,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Lista os arquivos presentes em um environment_id específico via Environments API."""
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


STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

