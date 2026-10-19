import os
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
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
    set_setting,
    update_session,
    upsert_turn,
)
from .interactions_client import (
    build_create_payload,
    cancel_interaction,
    create_interaction,
    download_environment_snapshot,
    extract_texts_from_interaction,
    format_session_as_markdown,
    get_interaction,
    recover_conversation_chain,
    resolve_api_key,
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


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    mode: Optional[str] = None
    agent_name: Optional[str] = None
    model_name: Optional[str] = None
    environment_id: Optional[str] = None
    last_interaction_id: Optional[str] = None
    max_total_tokens: Optional[int] = None
    system_instruction: Optional[str] = None


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


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    key = resolve_api_key()
    return {
        "status": "ok",
        "has_api_key": bool(key),
        "masked_key": f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("configurada" if key else ""),
    }


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


@app.post("/api/sessions")
def api_create_session(body: SessionCreateRequest) -> Dict[str, Any]:
    session = create_session(
        title=body.title or "Nova Conversa Antigravity",
        mode=body.mode or "agent",
        agent_name=body.agent_name or "antigravity-preview-09-2026",
        model_name=body.model_name or "gemini-3.8-flash",
        environment_id=body.environment_id or "",
        last_interaction_id=body.last_interaction_id or "",
        max_total_tokens=body.max_total_tokens or 0,
        system_instruction=body.system_instruction or "",
    )
    return session


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: str) -> Dict[str, Any]:
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return session


@app.patch("/api/sessions/{session_id}")
def api_update_session(session_id: str, body: SessionUpdateRequest) -> Dict[str, Any]:
    existing = get_session(session_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    updated = update_session(session_id, **body.model_dump(exclude_unset=True))
    return updated  # type: ignore


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
    )

    try:
        interaction_resp = await create_interaction(payload, api_key=body.api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    new_interaction_id = interaction_resp.get("id", "")
    returned_env_id = interaction_resp.get("environment_id") or env_id or ""
    u_text, out_text = extract_texts_from_interaction(interaction_resp, fallback_user_input=prompt)

    turns = session.get("turns", [])
    next_index = len(turns) + 1

    # Atualiza título automático se for o primeiro turno de uma sessão com título padrão
    new_title = session.get("title", "Nova Conversa Antigravity")
    if next_index == 1 and new_title in ("Nova Conversa Antigravity", "Nova Conversa"):
        new_title = prompt[:48] + ("..." if len(prompt) > 48 else "")

    upsert_turn(
        session_id=session_id,
        interaction_id=new_interaction_id,
        turn_index=next_index,
        previous_interaction_id=prev_id or "",
        environment_id=returned_env_id,
        user_input_text=u_text,
        output_text=out_text,
        status=interaction_resp.get("status", "completed"),
        steps=interaction_resp.get("steps", []) or [],
        usage=interaction_resp.get("usage", {}) or {},
        raw=interaction_resp,
        created_at=interaction_resp.get("created"),
    )

    update_session(
        session_id,
        title=new_title,
        mode=mode,
        agent_name=agent_name,
        model_name=model_name,
        environment_id=returned_env_id,
        last_interaction_id=new_interaction_id,
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

    if returned_env_id:
        update_session(session_id, environment_id=returned_env_id, last_interaction_id=interaction_id)

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
    try:
        resp = await cancel_interaction(interaction_id, api_key=api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await api_poll_turn(session_id, interaction_id, api_key=api_key)
    return {"status": "cancelled", "interaction": resp, "session": get_session(session_id)}


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


@app.get("/api/sessions/{session_id}/environment/download")
async def api_download_session_environment(
    session_id: str,
    path: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Response:
    """Baixa o snapshot (.tar.gz) do sandbox associado a esta sessão, tal como no AI Studio."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    env_id = (session.get("environment_id") or "").strip()
    if not env_id or env_id == "remote":
        raise HTTPException(
            status_code=400,
            detail="Nenhum ambiente remoto ativo nesta sessão. Envie uma mensagem primeiro para provisionar um environment_id.",
        )
    try:
        content, filename, content_type = await download_environment_snapshot(
            environment_id=env_id,
            path=path,
            api_key=api_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@app.get("/api/environments/{environment_id}/download")
async def api_download_environment_by_id(
    environment_id: str,
    path: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Response:
    """Baixa o snapshot (.tar.gz) diretamente pelo environment_id."""
    clean_id = environment_id.strip()
    if not clean_id or clean_id == "remote":
        raise HTTPException(
            status_code=400,
            detail="Informe um environment_id válido (ex: env_... ou UUID).",
        )
    try:
        content, filename, content_type = await download_environment_snapshot(
            environment_id=clean_id,
            path=path,
            api_key=api_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
