import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple
import httpx

from .database import get_setting

# Evita falha do httpx quando o ambiente define [::1] em NO_PROXY
for _proxy_var in ("NO_PROXY", "no_proxy"):
    if "[::1]" in os.environ.get(_proxy_var, ""):
        os.environ[_proxy_var] = os.environ[_proxy_var].replace(",[::1]", "").replace("[::1]", "")

INTERACTIONS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
ENVIRONMENTS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/environments"
FILES_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/files"


def resolve_api_key(explicit_key: Optional[str] = None) -> str:
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()
    saved_key = get_setting("gemini_api_key", "")
    if saved_key.strip():
        return saved_key.strip()
    return (
        os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("GOOGLE_API_KEY", "")
    ).strip()


def build_headers(api_key: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }
    key = resolve_api_key(api_key)
    if key:
        headers["x-goog-api-key"] = key
    return headers


def extract_texts_from_interaction(
    data: Dict[str, Any], fallback_user_input: str = ""
) -> Tuple[str, str]:
    """Extrai o texto de entrada do usuário e o texto final de saída do modelo/agente."""
    user_texts: List[str] = []
    output_texts: List[str] = []

    raw_input = data.get("input")
    if isinstance(raw_input, str) and raw_input.strip():
        user_texts.append(raw_input.strip())
    elif isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, dict) and item.get("type") == "text":
                user_texts.append(item.get("text", ""))

    for step in data.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        stype = step.get("type", "")
        contents = step.get("content", []) or []
        if stype == "user_input" and not user_texts:
            for c in contents:
                if isinstance(c, dict) and c.get("type") == "text":
                    user_texts.append(c.get("text", ""))
        elif stype == "model_output":
            for c in contents:
                if isinstance(c, dict) and c.get("type") == "text":
                    output_texts.append(c.get("text", ""))

    if not output_texts and isinstance(data.get("output_text"), str):
        output_texts.append(data["output_text"])

    user_input_str = "\n".join(t for t in user_texts if t).strip() or fallback_user_input
    output_str = "\n\n".join(t for t in output_texts if t).strip()
    return user_input_str, output_str


async def get_interaction(
    interaction_id: str,
    include_input: bool = True,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    params = {"include_input": "true"} if include_input else {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(
            f"{INTERACTIONS_BASE_URL}/{interaction_id}",
            params=params,
            headers=build_headers(api_key),
        )
        if resp.status_code >= 400:
            raise ValueError(
                f"Erro ao buscar interação '{interaction_id}' (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()


async def recover_conversation_chain(
    target_interaction_id: str,
    api_key: Optional[str] = None,
    max_depth: int = 100,
) -> List[Dict[str, Any]]:
    """
    Percorre a cadeia de previous_interaction_id de trás para frente a partir de
    qualquer interaction_id e devolve a lista completa de turnos em ordem cronológica.
    """
    chain: List[Dict[str, Any]] = []
    seen_ids = set()
    current_id: Optional[str] = target_interaction_id.strip()

    async with httpx.AsyncClient(timeout=60.0) as client:
        while current_id and len(chain) < max_depth:
            if current_id in seen_ids:
                break
            seen_ids.add(current_id)

            resp = await client.get(
                f"{INTERACTIONS_BASE_URL}/{current_id}",
                params={"include_input": "true"},
                headers=build_headers(api_key),
            )
            if resp.status_code >= 400:
                if not chain:
                    raise ValueError(
                        f"Não foi possível recuperar a interação '{current_id}' (HTTP {resp.status_code}): {resp.text}"
                    )
                break

            interaction_data = resp.json()
            chain.append(interaction_data)
            prev_id = interaction_data.get("previous_interaction_id")
            current_id = prev_id.strip() if isinstance(prev_id, str) and prev_id.strip() else None

    chain.reverse()
    return chain


def build_create_payload(
    prompt: str,
    mode: str = "agent",
    agent_name: str = "antigravity-preview-09-2026",
    model_name: str = "gemini-3.8-flash",
    previous_interaction_id: Optional[str] = None,
    environment_id: Optional[str] = None,
    max_total_tokens: int = 0,
    system_instruction: Optional[str] = None,
    background: bool = False,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "input": prompt,
        "store": True,
    }

    if previous_interaction_id and previous_interaction_id.strip():
        payload["previous_interaction_id"] = previous_interaction_id.strip()

    if background:
        payload["background"] = True

    if mode == "agent":
        payload["agent"] = agent_name or "antigravity-preview-09-2026"
        env_val = (environment_id or "").strip()

        # Se for um novo ambiente (sem environment_id prévio) e houver sources configuradas (ex: repositório GitHub)
        if (not env_val or env_val == "remote") and sources:
            clean_sources = []
            for s in sources:
                stype = s.get("type", "repository")
                if stype == "repository" and s.get("source"):
                    clean_sources.append({
                        "type": "repository",
                        "source": s["source"].strip(),
                        "target": (s.get("target") or "/workspace").strip(),
                    })
                elif stype == "gcs" and s.get("source"):
                    clean_sources.append({
                        "type": "gcs",
                        "source": s["source"].strip(),
                        "target": (s.get("target") or "/workspace").strip(),
                    })
                elif stype == "inline" and s.get("target"):
                    clean_sources.append({
                        "type": "inline",
                        "content": s.get("content", ""),
                        "target": s["target"].strip(),
                    })
            if clean_sources:
                payload["environment"] = {
                    "type": "remote",
                    "sources": clean_sources,
                }
            else:
                payload["environment"] = "remote"
        else:
            payload["environment"] = env_val if env_val else "remote"

        if "antigravity" in payload["agent"]:
            agent_cfg: Dict[str, Any] = {
                "type": "antigravity",
                "model": model_name or "gemini-3.8-flash",
            }
            if max_total_tokens and int(max_total_tokens) > 0:
                agent_cfg["max_total_tokens"] = int(max_total_tokens)
            payload["agent_config"] = agent_cfg
    else:
        payload["model"] = model_name or "gemini-3.8-flash"
        if system_instruction and system_instruction.strip():
            payload["system_instruction"] = system_instruction.strip()

    return payload


async def create_interaction(
    payload: Dict[str, Any],
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=300.0) as client:
        for attempt in range(3):
            resp = await client.post(
                INTERACTIONS_BASE_URL,
                json=payload,
                headers=build_headers(api_key),
            )
            if resp.status_code == 503 and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise ValueError(
                    f"Erro na Interactions API (HTTP {resp.status_code}): {resp.text}"
                )
            return resp.json()
        raise ValueError("Erro inesperado ao criar interação.")


async def cancel_interaction(
    interaction_id: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{INTERACTIONS_BASE_URL}/{interaction_id}/cancel",
            headers=build_headers(api_key),
        )
        if resp.status_code >= 400:
            # Fallback para sintaxe :cancel caso necessário
            resp = await client.post(
                f"{INTERACTIONS_BASE_URL}/{interaction_id}:cancel",
                headers=build_headers(api_key),
            )
        if resp.status_code >= 400:
            raise ValueError(
                f"Erro ao cancelar interação '{interaction_id}' (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()


def format_session_as_markdown(session: Dict[str, Any]) -> str:
    lines = [
        f"# {session.get('title', 'Conversa Antigravity Studio')}",
        "",
        f"- **Session ID:** `{session.get('id')}`",
        f"- **Modo:** `{session.get('mode')}`",
        f"- **Agente:** `{session.get('agent_name') or 'N/A'}`",
        f"- **Modelo:** `{session.get('model_name')}`",
        f"- **Environment ID:** `{session.get('environment_id') or 'remote (novo)'}`",
        f"- **Último Interaction ID:** `{session.get('last_interaction_id') or 'N/A'}`",
        "",
        "---",
        "",
    ]

    for idx, turn in enumerate(session.get("turns", []), start=1):
        lines.append(f"## Turno {idx}")
        lines.append(f"- **Interaction ID:** `{turn.get('interaction_id')}`")
        if turn.get("previous_interaction_id"):
            lines.append(f"- **Previous Interaction ID:** `{turn.get('previous_interaction_id')}`")
        if turn.get("environment_id"):
            lines.append(f"- **Environment ID:** `{turn.get('environment_id')}`")
        lines.append(f"- **Status:** `{turn.get('status')}`")
        lines.append("")

        user_text = turn.get("user_input_text") or "(Sem texto de entrada)"
        lines.append(f"### 👤 Usuário\n\n{user_text}\n")

        steps = turn.get("steps", []) or []
        for s in steps:
            stype = s.get("type", "")
            contents = s.get("content", []) or []
            if stype == "thought":
                t_text = "\n".join(c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text").strip()
                if t_text:
                    lines.append(f"<details><summary>💭 Pensamento do Agente</summary>\n\n{t_text}\n\n</details>\n")
            elif stype in ("function_call", "code_execution_call", "google_search_call"):
                name = s.get("name") or stype
                args = s.get("arguments") or s
                lines.append(f"🔧 **Tool Call (`{name}`):**\n```json\n{args}\n```\n")

        out_text = turn.get("output_text") or "(Sem resposta final textual)"
        lines.append(f"### 🤖 Antigravity / Gemini\n\n{out_text}\n")
        lines.append("---\n")

    return "\n".join(lines)


async def download_environment_snapshot(
    environment_id: str,
    path: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Tuple[bytes, str, str]:
    """
    Baixa o snapshot do ambiente remoto sandbox (container Linux) como arquivo compactado .tar.gz
    ou um arquivo/diretório específico. Idêntico à funcionalidade do botão Download no AI Studio.
    """
    clean_id = environment_id.strip()
    if clean_id.startswith("environments/"):
        clean_id = clean_id.replace("environments/", "")

    headers = build_headers(api_key)
    headers.pop("Content-Type", None)

    # 1. Tenta o endpoint de snapshot completo do ambiente (como no AI Studio)
    if not path:
        snapshot_url = f"{FILES_BASE_URL}/environment-{clean_id}:download?alt=media"
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            try:
                resp = await client.get(snapshot_url, headers=headers)
                if resp.status_code == 200 and resp.content:
                    ctype = resp.headers.get("content-type", "application/x-tar")
                    return resp.content, f"environment_snapshot_{clean_id}.tar.gz", ctype
            except Exception:
                pass

    # 2. Tenta o endpoint de arquivos do Environments API
    target_path = (path or "").strip().lstrip("/")
    path_url = (
        f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{target_path}?alt=media"
        if target_path
        else f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files?alt=media"
    )
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        resp = await client.get(path_url, headers=headers)
        if resp.status_code == 200 and resp.content:
            ctype = resp.headers.get("content-type", "application/x-tar")
            fname = f"environment_{clean_id}_{target_path or 'root'}.tar.gz"
            return resp.content, fname, ctype
        raise ValueError(
            f"Erro ao baixar snapshot do ambiente '{clean_id}' (HTTP {resp.status_code}): {resp.text}"
        )
