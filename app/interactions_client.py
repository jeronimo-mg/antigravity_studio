import asyncio
import io
import mimetypes
import os
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Dict, List, Optional, Tuple
import httpx

from .database import find_turn_by_interaction_id, get_setting

# Evita falha do httpx quando o ambiente define [::1] em NO_PROXY
for _proxy_var in ("NO_PROXY", "no_proxy"):
    if "[::1]" in os.environ.get(_proxy_var, ""):
        os.environ[_proxy_var] = os.environ[_proxy_var].replace(",[::1]", "").replace("[::1]", "")

INTERACTIONS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
ENVIRONMENTS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/environments"
ENVIRONMENTS_UPLOAD_BASE_URL = "https://generativelanguage.googleapis.com/upload/v1beta/environments"
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
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                user_texts.append(item.get("text", ""))
            elif item.get("type") == "user_input" or "content" in item:
                for c in item.get("content", []) or []:
                    if isinstance(c, dict) and c.get("type") == "text":
                        user_texts.append(c.get("text", ""))

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

    local_user = (data.get("_local_user_input_text") or "").strip()
    if not local_user and data.get("id"):
        known_turn = find_turn_by_interaction_id(str(data.get("id")))
        if known_turn and known_turn.get("user_input_text"):
            local_user = known_turn["user_input_text"].strip()

    user_input_str = (
        "\n".join(t for t in user_texts if t).strip()
        or local_user
        or fallback_user_input
    )
    marker = "\n\n---\n[NOTA TÉCNICA DO AMBIENTE SANDBOX"
    if marker in user_input_str:
        user_input_str = user_input_str.split(marker, 1)[0].strip()
    if "[NOVA INSTRUÇÃO DO USUÁRIO]:\n" in user_input_str:
        user_input_str = user_input_str.split("[NOVA INSTRUÇÃO DO USUÁRIO]:\n", 1)[1].strip()
    output_str = "\n\n".join(t for t in output_texts if t).strip()
    return user_input_str, output_str


async def get_interaction(
    interaction_id: str,
    include_input: bool = True,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    params = {"include_input": "true"} if include_input else {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(3):
            try:
                resp = await client.get(
                    f"{INTERACTIONS_BASE_URL}/{interaction_id}",
                    params=params,
                    headers=build_headers(api_key),
                )
            except httpx.TimeoutException:
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise ValueError(f"Timeout ao consultar interação '{interaction_id}'.")

            if resp.status_code in (500, 502, 503, 504) and attempt < 2:
                if include_input and attempt == 1:
                    params = {}
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code >= 400:
                raise ValueError(
                    f"Erro ao buscar interação '{interaction_id}' (HTTP {resp.status_code}): {resp.text}"
                )
            return resp.json()
        raise ValueError(f"Erro inesperado ao buscar interação '{interaction_id}'.")


async def recover_conversation_chain(
    target_interaction_id: str,
    api_key: Optional[str] = None,
    max_depth: int = 100,
) -> List[Dict[str, Any]]:
    """
    Percorre a cadeia de previous_interaction_id de trás para frente a partir de
    qualquer interaction_id (combinando dados da Interactions API com o banco SQLite local)
    e devolve a lista completa de turnos em ordem cronológica.
    """
    chain: List[Dict[str, Any]] = []
    seen_ids = set()
    current_id: Optional[str] = target_interaction_id.strip()

    async with httpx.AsyncClient(timeout=60.0) as client:
        while current_id and len(chain) < max_depth:
            if current_id in seen_ids:
                break
            seen_ids.add(current_id)

            local_turn = find_turn_by_interaction_id(current_id)
            interaction_data: Optional[Dict[str, Any]] = None

            try:
                resp = await client.get(
                    f"{INTERACTIONS_BASE_URL}/{current_id}",
                    params={"include_input": "true"},
                    headers=build_headers(api_key),
                )
                if resp.status_code < 400:
                    interaction_data = resp.json()
            except Exception:
                interaction_data = None

            if not interaction_data and local_turn:
                raw_local = dict(local_turn.get("raw") or {})
                raw_local.setdefault("id", current_id)
                raw_local.setdefault("previous_interaction_id", local_turn.get("previous_interaction_id", ""))
                raw_local.setdefault("environment_id", local_turn.get("environment_id", ""))
                raw_local.setdefault("status", local_turn.get("status", "completed"))
                raw_local.setdefault("steps", local_turn.get("steps", []))
                raw_local.setdefault("usage", local_turn.get("usage", {}))
                raw_local.setdefault("output_text", local_turn.get("output_text", ""))
                raw_local["_local_user_input_text"] = local_turn.get("user_input_text", "")
                interaction_data = raw_local

            if not interaction_data:
                if not chain:
                    raise ValueError(
                        f"Não foi possível recuperar a interação '{current_id}' na API nem no banco local."
                    )
                break

            if local_turn and local_turn.get("user_input_text"):
                interaction_data["_local_user_input_text"] = local_turn["user_input_text"]

            chain.append(interaction_data)
            prev_id = interaction_data.get("previous_interaction_id") or (
                local_turn.get("previous_interaction_id") if local_turn else None
            )
            current_id = prev_id.strip() if isinstance(prev_id, str) and prev_id.strip() else None

    chain.reverse()
    return chain


def enrich_agent_prompt_for_sandbox(
    prompt: str,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Adiciona instruções técnicas transparentes para o agente Antigravity quando há arquivos
    enviados via Environments API ou quando o prompt menciona arquivos .pdf, .odt, .odp, .docx, etc.
    Isso evita que o agente chame `view_file` diretamente em binários (.odt/.odp) ou PDFs grandes,
    o que causa `HTTP 500: Internal error encountered` na Interactions API (que suporta apenas Text/Image).
    """
    if prompt.strip().lower() == "continue":
        return prompt

    has_doc_ext = bool(
        re.search(r"\.(pdf|odt|odp|ods|docx|pptx|xlsx)\b", prompt, flags=re.IGNORECASE)
    )
    has_uploads = bool(uploaded_files)
    if not has_doc_ext and not has_uploads:
        return prompt

    notes: List[str] = [
        "\n\n---",
        "[NOTA TÉCNICA DO AMBIENTE SANDBOX (ANTIGRAVITY STUDIO)]:",
    ]
    if uploaded_files:
        paths = []
        for uf in uploaded_files:
            tp = uf.get("target_path")
            if tp and tp not in paths:
                paths.append(tp)
        if paths:
            formatted_paths = ", ".join(f"`{p}`" for p in paths)
            notes.append(
                f"- Arquivos/pastas enviados via Environments API estão localizados em: {formatted_paths} (use `find_by_name` ou `execute_bash` com `ls -la` para listar os nomes exatos)."
            )

    notes.extend([
        "- REGRA CRÍTICA (PREVENÇÃO DE ERRO HTTP 500 NA API): NUNCA use a ferramenta `view_file` diretamente em arquivos `.pdf`, `.odt`, `.odp`, `.ods`, `.docx`, `.pptx` ou `.xlsx`. A ferramenta `view_file` injeta o binário completo no histórico multimodal, o que falha para formatos OpenDocument (`.odt`/`.odp`) e PDFs grandes com erro `HTTP 500: Internal error encountered`.",
        "- COMO LER ESSES DOCUMENTOS COM SEGURANÇA:",
        "  1. Verifique primeiro se já existem arquivos `.extracted.txt` gerados automaticamente ao lado dos documentos (ex: `<nome_do_arquivo>.extracted.txt`) ou o script `_extract_docs_helper.py` no diretório de upload.",
        "  2. Caso precise extrair texto de `.odt` ou `.odp` (OpenDocument XML), use `execute_bash` com `python3` lendo `content.xml` via `zipfile.ZipFile` e `xml.etree.ElementTree` (bibliotecas nativas do Python 3.12).",
        "  3. Caso precise extrair texto de `.pdf`, use `execute_bash` com `python3` (`pypdf`, `pymupdf`/`fitz` ou `pdfplumber`, instalando via `pip install --break-system-packages pypdf pymupdf` se necessário) ou `pdftotext`, salvando o texto em `/tmp/*.txt` e inspecionando por partes/seções.",
    ])
    return prompt + "\n".join(notes)


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
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    effective_prompt = (
        enrich_agent_prompt_for_sandbox(prompt, uploaded_files=uploaded_files)
        if mode == "agent"
        else prompt
    )
    payload: Dict[str, Any] = {
        "input": effective_prompt,
        "store": True,
    }

    if previous_interaction_id and previous_interaction_id.strip():
        payload["previous_interaction_id"] = previous_interaction_id.strip()

    if background:
        payload["background"] = True

    if mode == "agent":
        payload["agent"] = agent_name or "antigravity-preview-09-2026"
        env_val = clean_environment_id(environment_id or "")

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
    fallback_history_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cria uma interação na Interactions API com proteção resiliente contra erros HTTP 400/500/502/503/504:
    1. Se uma chamada síncrona (`background=False`) para um agente demorar demais ou falhar com HTTP 500/502/503/504/Timeout,
       ativa automaticamente `background=True` para evitar timeout no gateway HTTP da Google.
    2. Se `previous_interaction_id` for proveniente de uma sessão/turno que deu erro (HTTP 400 ou HTTP 500),
       desvincula automaticamente o `previous_interaction_id` defeituoso mas PRESERVA o `environment`
       (container Linux com todos os arquivos enviados), permitindo continuar no mesmo sandbox sem erro.
    """
    current_payload = dict(payload)
    is_agent = bool(current_payload.get("agent"))
    dropped_prev_id: Optional[str] = None

    async with httpx.AsyncClient(timeout=300.0) as client:
        for attempt in range(4):
            try:
                resp = await client.post(
                    INTERACTIONS_BASE_URL,
                    json=current_payload,
                    headers=build_headers(api_key),
                )
            except (httpx.TimeoutException, httpx.TransportError):
                if is_agent and not current_payload.get("background") and attempt < 3:
                    current_payload["background"] = True
                    continue
                if attempt < 3:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise ValueError(
                    "Timeout ou falha de transporte ao aguardar resposta da Interactions API. Ative 'Execução em Background' para tarefas longas."
                )

            if resp.status_code < 400:
                data = resp.json()
                if isinstance(data, dict) and dropped_prev_id:
                    data["_dropped_previous_interaction_id"] = dropped_prev_id
                return data

            # Se previous_interaction_id veio de uma interação que falhou/deu erro (HTTP 400 ou 404 referenciando previous_interaction_id),
            # remove o previous_interaction_id mantendo o mesmo environment (sandbox) imediatamente!
            if resp.status_code in (400, 404) and current_payload.get("previous_interaction_id") and attempt < 3:
                dropped_prev_id = str(current_payload.pop("previous_interaction_id", ""))
                if fallback_history_text:
                    current_payload["input"] = (
                        f"[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n{fallback_history_text}\n\n"
                        f"[NOVA INSTRUÇÃO DO USUÁRIO]:\n{current_payload.get('input', '')}"
                    )
                await asyncio.sleep(0.8)
                continue

            # Tratamento resiliente para HTTP 500 / 502 / 503 / 504
            if resp.status_code in (500, 502, 503, 504) and attempt < 3:
                # Passo 1: Se for agente rodando síncrono (background=False), muda para background=True imediatamente
                if is_agent and not current_payload.get("background"):
                    current_payload["background"] = True
                    await asyncio.sleep(1.0)
                    continue

                # Passo 2: Se tinha previous_interaction_id (ex: turno anterior deu erro HTTP 500),
                # desvincula o previous_interaction_id corrompido já na próxima tentativa mantendo o mesmo environment_id!
                if current_payload.get("previous_interaction_id"):
                    dropped_prev_id = str(current_payload.pop("previous_interaction_id", ""))
                    if fallback_history_text:
                        current_payload["input"] = (
                            f"[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n{fallback_history_text}\n\n"
                            f"[NOVA INSTRUÇÃO DO USUÁRIO]:\n{current_payload.get('input', '')}"
                        )
                    await asyncio.sleep(1.0)
                    continue

                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            raise ValueError(
                f"Erro na Interactions API (HTTP {resp.status_code}): {resp.text}"
            )
        raise ValueError("Erro inesperado ao criar interação.")


async def cancel_interaction(
    interaction_id: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cancela uma interação em background na Interactions API:
      POST /v1beta/interactions/{interaction_id}/cancel
    Usa headers sem 'Content-Type: application/json' quando o body é vazio (igual ao SDK oficial google-genai)
    e tenta fallback com body '{}' ou ':cancel', além de tratar graciosamente interações que já terminaram.
    """
    clean_id = (interaction_id or "").strip()
    if not clean_id:
        raise ValueError("O interaction_id é obrigatório para cancelar a interação.")

    # Headers sem Content-Type: application/json para POST com body vazio (evita HTTP 400 Invalid JSON payload no GFE)
    empty_body_headers = build_headers(api_key)
    empty_body_headers.pop("Content-Type", None)
    empty_body_headers["Accept"] = "application/json"

    json_headers = build_headers(api_key)
    json_headers["Accept"] = "application/json"

    attempts = [
        (f"{INTERACTIONS_BASE_URL}/{clean_id}/cancel", empty_body_headers, b""),
        (f"{INTERACTIONS_BASE_URL}/{clean_id}/cancel", json_headers, b"{}"),
        (f"{INTERACTIONS_BASE_URL}/{clean_id}:cancel", empty_body_headers, b""),
        (f"{INTERACTIONS_BASE_URL}/{clean_id}:cancel", json_headers, b"{}"),
        (f"https://generativelanguage.googleapis.com/v1alpha/interactions/{clean_id}/cancel", empty_body_headers, b""),
    ]

    last_status = 0
    last_text = ""

    async with httpx.AsyncClient(timeout=30.0) as client:
        for url, hdrs, body_bytes in attempts:
            try:
                resp = await client.post(url, headers=hdrs, content=body_bytes)
            except Exception as exc:
                last_text = str(exc)
                continue

            if resp.status_code < 400:
                if not resp.content:
                    return {"id": clean_id, "status": "cancelled"}
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        if not data.get("status"):
                            data["status"] = "cancelled"
                        return data
                except Exception:
                    pass
                return {"id": clean_id, "status": "cancelled"}

            last_status = resp.status_code
            last_text = resp.text

            # Se retornou 400/409 porque a interação já não está mais in_progress (ex: acabou de concluir ou já foi cancelada),
            # consulta o estado atual via GET e retorna sem estourar erro
            if resp.status_code in (400, 409, 412):
                try:
                    current_state = await get_interaction(clean_id, include_input=True, api_key=api_key)
                    cur_status = current_state.get("status", "")
                    if cur_status and cur_status != "in_progress":
                        return current_state
                except Exception:
                    pass

        # Fallback final: se o endpoint de cancelamento do agente não suportar cancelamento remoto síncrono,
        # verifica se conseguimos ler a interação e marca como cancelled localmente para destravar a UI do usuário
        try:
            current_state = await get_interaction(clean_id, include_input=True, api_key=api_key)
            current_state["status"] = "cancelled"
            current_state["_locally_cancelled"] = True
            return current_state
        except Exception:
            pass

        raise ValueError(
            f"Erro ao cancelar interação '{clean_id}' (HTTP {last_status}): {last_text}"
        )


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
    ]

    uploaded_files = session.get("uploaded_files") or []
    if uploaded_files:
        lines.append("- **Arquivos Enviados via Environments API:**")
        for uf in uploaded_files:
            lines.append(
                f"  - `{uf.get('target_path')}` ({uf.get('size_bytes', 0)} bytes, extract={uf.get('extract', False)})"
            )

    lines.extend(["", "---", ""])

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


def clean_environment_id(environment_id: str) -> str:
    clean_id = (environment_id or "").strip()
    if clean_id.startswith("environments/"):
        clean_id = clean_id.replace("environments/", "", 1)
    return clean_id


async def create_remote_environment(
    sources: Optional[List[Dict[str, Any]]] = None,
    from_environment: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Provisiona um novo ambiente remoto sandbox via Environments API (POST /v1beta/environments),
    permitindo obter um environment_id antes mesmo do primeiro prompt (ex: para fazer upload direto
    de arquivos locais ou pastas via Environments API).
    """
    body: Dict[str, Any] = {}
    if from_environment and from_environment.strip():
        clean_from = clean_environment_id(from_environment)
        body["from_environment"] = f"environments/{clean_from}"
    elif sources:
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
            body["sources"] = clean_sources

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            ENVIRONMENTS_BASE_URL,
            json=body,
            headers=build_headers(api_key),
        )
        if resp.status_code >= 400:
            raise ValueError(
                f"Erro ao provisionar ambiente na Environments API (HTTP {resp.status_code}): {resp.text}"
            )
        data = resp.json()
        env_id = data.get("id") or data.get("name", "")
        if env_id:
            data["id"] = clean_environment_id(env_id)
        return data


def extract_text_from_document_bytes(filename: str, content: bytes) -> Optional[str]:
    """
    Extrai texto puro em memória a partir de arquivos OpenDocument (.odt, .odp, .ods),
    Office XML (.docx, .pptx, .xlsx) ou PDF (.pdf, se pypdf/pymupdf estiver instalado).
    Isso permite disponibilizar versões .extracted.txt já prontas no sandbox, evitando que o
    agente use `view_file` em arquivos binários (o que gera HTTP 500 na Interactions API).
    """
    lower_name = (filename or "").lower()
    try:
        if lower_name.endswith((".odt", ".odp", ".ods")):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if "content.xml" not in zf.namelist():
                    return None
                xml_bytes = zf.read("content.xml")
                root = ET.fromstring(xml_bytes)
                paragraphs: List[str] = []
                for elem in root.iter():
                    tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if tag in ("p", "h", "list-item", "table-cell"):
                        txt = "".join(elem.itertext()).strip()
                        if txt:
                            paragraphs.append(txt)
                if not paragraphs:
                    raw_txt = " ".join(t.strip() for t in root.itertext() if t and t.strip())
                    return raw_txt if raw_txt else None
                return "\n".join(paragraphs)

        if lower_name.endswith(".docx"):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if "word/document.xml" not in zf.namelist():
                    return None
                root = ET.fromstring(zf.read("word/document.xml"))
                lines = ["".join(el.itertext()).strip() for el in root.iter() if el.tag.endswith("}p")]
                return "\n".join(l for l in lines if l)

        if lower_name.endswith(".pptx"):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                slide_files = sorted(
                    n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                )
                slides_out: List[str] = []
                for idx, sf in enumerate(slide_files, start=1):
                    root = ET.fromstring(zf.read(sf))
                    txt = " ".join(t.strip() for t in root.itertext() if t and t.strip())
                    if txt:
                        slides_out.append(f"--- Slide {idx} ---\n{txt}")
                return "\n\n".join(slides_out) if slides_out else None

        if lower_name.endswith(".pdf"):
            try:
                import pypdf  # type: ignore
                reader = pypdf.PdfReader(io.BytesIO(content))
                pages_out: List[str] = []
                for idx, page in enumerate(reader.pages, start=1):
                    ptxt = (page.extract_text() or "").strip()
                    if ptxt:
                        pages_out.append(f"=== Página {idx} ===\n{ptxt}")
                if pages_out:
                    return "\n\n".join(pages_out)
            except Exception:
                pass
            try:
                import fitz  # type: ignore
                doc = fitz.open(stream=content, filetype="pdf")
                pages_out = []
                for idx, page in enumerate(doc, start=1):
                    ptxt = (page.get_text("text") or "").strip()
                    if ptxt:
                        pages_out.append(f"=== Página {idx} ===\n{ptxt}")
                if pages_out:
                    return "\n\n".join(pages_out)
            except Exception:
                pass
    except Exception:
        return None
    return None


SANDBOX_DOC_EXTRACTOR_SCRIPT = '''#!/usr/bin/env python3
"""
Script utilitário no sandbox para extrair texto de arquivos .pdf, .odt, .odp, .docx e .pptx
sem usar view_file em binários (evitando erro HTTP 500 na Interactions API).
Uso: python3 _extract_docs_helper.py <caminho_arquivo_ou_pasta>
"""
import os, sys, zipfile, xml.etree.ElementTree as ET

def extract_odf(path):
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("content.xml"))
        return "\n".join("".join(el.itertext()).strip() for el in root.iter() if el.tag.split("}")[-1] in ("p", "h") and "".join(el.itertext()).strip())

def extract_pdf(path):
    try:
        import pypdf
    except ImportError:
        os.system("pip install -q --break-system-packages pypdf")
        import pypdf
    reader = pypdf.PdfReader(path)
    return "\n\n".join(f"=== Página {i+1} ===\n" + (p.extract_text() or "") for i, p in enumerate(reader.pages))

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    paths = [target] if os.path.isfile(target) else [
        os.path.join(r, f) for r, _, fs in os.walk(target) for f in fs
    ]
    for p in paths:
        low = p.lower()
        out_p = p + ".extracted.txt"
        try:
            if low.endswith((".odt", ".odp", ".ods")) and not os.path.exists(out_p):
                txt = extract_odf(p)
                open(out_p, "w", encoding="utf-8").write(txt)
                print(f"Extraído: {out_p} ({len(txt)} chars)")
            elif low.endswith(".pdf") and not os.path.exists(out_p):
                txt = extract_pdf(p)
                open(out_p, "w", encoding="utf-8").write(txt)
                print(f"Extraído: {out_p} ({len(txt)} chars)")
        except Exception as e:
            print(f"Erro em {p}: {e}")
'''


def pack_files_as_tar_gz(files_list: List[Tuple[str, bytes]]) -> bytes:
    """
    Compacta em memória uma lista de arquivos (caminho_relativo, conteudo_bytes) em formato .tar.gz.
    Usado para enviar uma pasta local inteira ou múltiplos arquivos de uma só vez com extract=True.
    Para documentos (.odt, .odp, .docx, .pptx, .pdf), também inclui automaticamente a versão
    `.extracted.txt` e o script `_extract_docs_helper.py` para leitura segura pelo agente no sandbox.
    """
    buf = io.BytesIO()
    has_complex_docs = False
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel_path, content in files_list:
            clean_rel = (rel_path or "arquivo").replace("\\", "/").lstrip("/")
            if not clean_rel:
                clean_rel = "arquivo"
            info = tarfile.TarInfo(name=clean_rel)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))

            if clean_rel.lower().endswith((".pdf", ".odt", ".odp", ".ods", ".docx", ".pptx")):
                has_complex_docs = True
                extracted_text = extract_text_from_document_bytes(clean_rel, content)
                if extracted_text:
                    txt_bytes = extracted_text.encode("utf-8")
                    txt_rel = f"{clean_rel}.extracted.txt"
                    txt_info = tarfile.TarInfo(name=txt_rel)
                    txt_info.size = len(txt_bytes)
                    txt_info.mode = 0o644
                    tar.addfile(txt_info, io.BytesIO(txt_bytes))

        if has_complex_docs:
            helper_bytes = SANDBOX_DOC_EXTRACTOR_SCRIPT.encode("utf-8")
            helper_info = tarfile.TarInfo(name="_extract_docs_helper.py")
            helper_info.size = len(helper_bytes)
            helper_info.mode = 0o755
            tar.addfile(helper_info, io.BytesIO(helper_bytes))

    buf.seek(0)
    return buf.read()


def _normalize_upload_response(
    raw_resp: Any,
    clean_id: str,
    clean_path: str,
    size_bytes: int,
    mime_type: str,
    extract: bool,
    overwrite: bool,
    upload_mode: str,
) -> Dict[str, Any]:
    files_out: List[Dict[str, Any]] = []
    if isinstance(raw_resp, dict):
        if isinstance(raw_resp.get("files"), list):
            for f in raw_resp["files"]:
                if isinstance(f, dict):
                    files_out.append({
                        "name": f.get("name") or os.path.basename(f.get("path") or clean_path),
                        "path": f.get("path") or clean_path,
                        "size_bytes": int(f.get("size_bytes") or f.get("sizeBytes") or size_bytes),
                        "mime_type": f.get("mime_type") or f.get("mimeType") or mime_type,
                        "type": f.get("type") or "file",
                    })
        elif isinstance(raw_resp.get("file"), dict):
            f = raw_resp["file"]
            files_out.append({
                "name": f.get("name") or os.path.basename(f.get("path") or clean_path),
                "path": f.get("path") or clean_path,
                "size_bytes": int(f.get("size_bytes") or f.get("sizeBytes") or size_bytes),
                "mime_type": f.get("mime_type") or f.get("mimeType") or mime_type,
                "type": f.get("type") or "file",
            })
        elif "name" in raw_resp or "path" in raw_resp:
            files_out.append({
                "name": raw_resp.get("name") or os.path.basename(raw_resp.get("path") or clean_path),
                "path": raw_resp.get("path") or clean_path,
                "size_bytes": int(raw_resp.get("size_bytes") or raw_resp.get("sizeBytes") or size_bytes),
                "mime_type": raw_resp.get("mime_type") or raw_resp.get("mimeType") or mime_type,
                "type": raw_resp.get("type") or "file",
            })

    if not files_out:
        files_out.append({
            "name": os.path.basename(clean_path.rstrip("/")) or "archive",
            "path": clean_path,
            "size_bytes": size_bytes,
            "mime_type": mime_type,
            "type": "directory" if extract else "file",
        })

    return {
        "environment_id": clean_id,
        "target_path": f"/{clean_path}",
        "size_bytes": size_bytes,
        "mime_type": mime_type,
        "extract": extract,
        "overwrite": overwrite,
        "upload_mode": upload_mode,
        "files": files_out,
        "raw": raw_resp if isinstance(raw_resp, dict) else {},
    }


async def upload_file_to_environment(
    environment_id: str,
    target_path: str,
    file_bytes: bytes,
    mime_type: Optional[str] = None,
    overwrite: bool = True,
    extract: bool = False,
    resumable: bool = False,
    chunk_size: int = 4 * 1024 * 1024,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Envia um arquivo individual ou pacote (.tar / .tar.gz com extract=True) diretamente para
    um ambiente existente usando a Environments API:
      PUT https://generativelanguage.googleapis.com/upload/v1beta/environments/{environment_id}/files/{path}

    Suporta:
    1. Upload direto via HTTP PUT (arquivo único ou arquivo .tar/.tar.gz com ?extract=true&overwrite=true)
    2. Upload Resumível em partes (uploadType=resumable com Content-Range ou protocolo Scotty x-goog-upload-url)
    """
    clean_id = clean_environment_id(environment_id)
    if not clean_id or clean_id == "remote":
        raise ValueError(
            "É necessário um environment_id válido (ex: env_...) para fazer upload de arquivos."
        )

    clean_path = (target_path or "").strip().lstrip("/")
    if not clean_path:
        raise ValueError(
            "O caminho de destino (path) dentro do ambiente não pode ser vazio ou a raiz '/'."
        )

    size_bytes = len(file_bytes)
    if not mime_type:
        if extract or clean_path.endswith((".tar.gz", ".tgz", ".tar")):
            mime_type = "application/x-tar"
        else:
            guessed, _ = mimetypes.guess_type(clean_path)
            mime_type = guessed or "application/octet-stream"

    query_params: Dict[str, str] = {}
    if overwrite is not None:
        query_params["overwrite"] = "true" if overwrite else "false"
    if extract:
        query_params["extract"] = "true"

    upload_endpoint = f"{ENVIRONMENTS_UPLOAD_BASE_URL}/{clean_id}/files/{clean_path}"
    base_headers = build_headers(api_key)
    base_headers.pop("Content-Type", None)

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        # Modo Resumível explícito (uploadType=resumable)
        if resumable:
            resumable_params = {**query_params, "uploadType": "resumable"}
            init_headers = {
                **base_headers,
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(size_bytes),
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size_bytes),
                "X-Goog-Upload-Header-Content-Type": mime_type,
            }
            init_resp = await client.put(
                upload_endpoint,
                params=resumable_params,
                headers=init_headers,
                content=b"",
            )
            if init_resp.status_code >= 400:
                raise ValueError(
                    f"Erro ao iniciar sessão de upload resumível (HTTP {init_resp.status_code}): {init_resp.text}"
                )

            scotty_url = (
                init_resp.headers.get("x-goog-upload-url")
                or init_resp.headers.get("X-Goog-Upload-URL")
            )
            location_url = (
                init_resp.headers.get("location")
                or init_resp.headers.get("Location")
            )
            session_url = scotty_url or location_url

            # Caso o servidor já tenha aceitado na chamada inicial sem devolver URL de sessão
            if not session_url:
                # Faz fallback para envio direto dos bytes via PUT
                direct_headers = {**base_headers, "Content-Type": mime_type}
                put_resp = await client.put(
                    upload_endpoint,
                    params=query_params,
                    headers=direct_headers,
                    content=file_bytes,
                )
                if put_resp.status_code >= 400:
                    raise ValueError(
                        f"Erro no upload para o ambiente '{clean_id}' (HTTP {put_resp.status_code}): {put_resp.text}"
                    )
                raw_data = put_resp.json() if put_resp.content else {}
                return _normalize_upload_response(
                    raw_data, clean_id, clean_path, size_bytes, mime_type, extract, overwrite, "direct_put"
                )

            if scotty_url and not location_url:
                # Protocolo Scotty (usado pelo SDK google-genai)
                offset = 0
                step = max(chunk_size, 256 * 1024)
                last_resp = None
                if size_bytes == 0:
                    last_resp = await client.post(
                        session_url,
                        headers={
                            **base_headers,
                            "X-Goog-Upload-Command": "upload, finalize",
                            "X-Goog-Upload-Offset": "0",
                            "Content-Type": mime_type,
                        },
                        content=b"",
                    )
                else:
                    while offset < size_bytes:
                        end = min(offset + step, size_bytes)
                        chunk = file_bytes[offset:end]
                        is_last = end >= size_bytes
                        cmd = "upload, finalize" if is_last else "upload"
                        last_resp = await client.post(
                            session_url,
                            headers={
                                **base_headers,
                                "X-Goog-Upload-Command": cmd,
                                "X-Goog-Upload-Offset": str(offset),
                                "Content-Type": mime_type,
                            },
                            content=chunk,
                        )
                        if last_resp.status_code >= 400:
                            raise ValueError(
                                f"Erro ao enviar parte do arquivo (HTTP {last_resp.status_code}): {last_resp.text}"
                            )
                        offset = end

                raw_data = last_resp.json() if (last_resp and last_resp.content) else {}
                return _normalize_upload_response(
                    raw_data, clean_id, clean_path, size_bytes, mime_type, extract, overwrite, "resumable"
                )
            else:
                # Protocolo Resumable HTTP PUT com Content-Range (Location header)
                offset = 0
                step = max(chunk_size, 256 * 1024)
                last_resp = None
                if size_bytes == 0:
                    last_resp = await client.put(
                        session_url,
                        headers={
                            **base_headers,
                            "Content-Type": mime_type,
                            "Content-Range": "bytes */0",
                        },
                        content=b"",
                    )
                else:
                    while offset < size_bytes:
                        end = min(offset + step, size_bytes)
                        chunk = file_bytes[offset:end]
                        content_range = f"bytes {offset}-{end - 1}/{size_bytes}"
                        last_resp = await client.put(
                            session_url,
                            headers={
                                **base_headers,
                                "Content-Type": mime_type,
                                "Content-Range": content_range,
                            },
                            content=chunk,
                        )
                        if last_resp.status_code not in (200, 201, 308):
                            raise ValueError(
                                f"Erro no chunk do upload resumível (HTTP {last_resp.status_code}): {last_resp.text}"
                            )
                        offset = end

                raw_data = last_resp.json() if (last_resp and last_resp.content) else {}
                return _normalize_upload_response(
                    raw_data, clean_id, clean_path, size_bytes, mime_type, extract, overwrite, "resumable"
                )

        # Modo Padrão: HTTP PUT direto em /upload/v1beta/environments/{env_id}/files/{path}
        direct_headers = {
            **base_headers,
            "Content-Type": mime_type,
        }
        resp = await client.put(
            upload_endpoint,
            params=query_params,
            headers=direct_headers,
            content=file_bytes,
        )

        # Se o endpoint respondeu com handshake Scotty (x-goog-upload-url) em vez de receber os bytes direto
        scotty_url = (
            resp.headers.get("x-goog-upload-url")
            or resp.headers.get("X-Goog-Upload-URL")
        )
        if resp.status_code == 200 and scotty_url:
            finalize_resp = await client.post(
                scotty_url,
                headers={
                    **base_headers,
                    "X-Goog-Upload-Command": "upload, finalize",
                    "X-Goog-Upload-Offset": "0",
                    "Content-Type": mime_type,
                },
                content=file_bytes,
            )
            if finalize_resp.status_code >= 400:
                raise ValueError(
                    f"Erro ao finalizar upload no ambiente '{clean_id}' (HTTP {finalize_resp.status_code}): {finalize_resp.text}"
                )
            raw_data = finalize_resp.json() if finalize_resp.content else {}
            return _normalize_upload_response(
                raw_data, clean_id, clean_path, size_bytes, mime_type, extract, overwrite, "scotty_resumable"
            )

        if resp.status_code >= 400:
            raise ValueError(
                f"Erro ao fazer upload para o ambiente '{clean_id}' em '{clean_path}' (HTTP {resp.status_code}): {resp.text}"
            )

        try:
            raw_data = resp.json() if resp.content else {}
        except Exception:
            raw_data = {}

        return _normalize_upload_response(
            raw_data, clean_id, clean_path, size_bytes, mime_type, extract, overwrite, "direct_put"
        )


def _extract_files_list_from_response(raw_data: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_data, dict):
        return []
    raw_list = (
        raw_data.get("files")
        if isinstance(raw_data.get("files"), list)
        else (raw_data.get("entries") if isinstance(raw_data.get("entries"), list) else [])
    )
    normalized: List[Dict[str, Any]] = []
    for item in raw_list:
        if isinstance(item, str):
            clean_p = item.lstrip("/")
            normalized.append({
                "name": os.path.basename(clean_p) or clean_p,
                "path": clean_p,
                "size_bytes": 0,
                "mime_type": "",
                "type": "file",
            })
        elif isinstance(item, dict):
            raw_p = str(item.get("path") or item.get("name") or "").lstrip("/")
            raw_type = str(item.get("type") or "").lower()
            is_dir = (
                raw_type in ("directory", "dir", "folder")
                or bool(item.get("is_dir"))
                or item.get("mime_type") == "inode/directory"
                or item.get("mimeType") == "inode/directory"
            )
            size_val = item.get("size_bytes")
            if size_val is None:
                size_val = item.get("sizeBytes", item.get("size", 0))
            try:
                size_int = int(size_val or 0)
            except (ValueError, TypeError):
                size_int = 0
            normalized.append({
                "name": item.get("name") or os.path.basename(raw_p.rstrip("/")) or raw_p,
                "path": raw_p,
                "size_bytes": size_int,
                "mime_type": item.get("mime_type") or item.get("mimeType") or "",
                "type": "directory" if is_dir else "file",
                "modified": item.get("modified") or item.get("updated") or "",
                "created": item.get("created") or "",
            })
    return normalized


async def list_environment_files(
    environment_id: str,
    path: str = "workspace",
    recursive: bool = True,
    known_paths: Optional[List[str]] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Lista os arquivos e diretórios presentes dentro do ambiente sandbox via Environments API:
      GET https://generativelanguage.googleapis.com/v1beta/environments/{environment_id}/files/{path}
    Se o diretório solicitado (ex: 'workspace') retornar 404 ou estiver vazio, tenta automaticamente
    caminhos alternativos conhecidos (como os diretórios de upload registrados na sessão ou a raiz).
    """
    import urllib.parse

    clean_id = clean_environment_id(environment_id)
    if not clean_id or clean_id == "remote":
        raise ValueError("Informe um environment_id válido (ex: env_...).")

    clean_path = (path or "").strip().lstrip("/")
    candidates: List[str] = [clean_path]
    if known_paths:
        for kp in known_paths:
            ckp = (kp or "").strip().lstrip("/")
            if ckp and ckp not in candidates:
                candidates.append(ckp)
            top_seg = ckp.split("/")[0] if ckp else ""
            if top_seg and top_seg not in candidates:
                candidates.append(top_seg)
    for fallback_p in ("workspace", "", "."):
        if fallback_p not in candidates:
            candidates.append(fallback_p)

    params: Dict[str, str] = {}
    if recursive:
        params["recursive"] = "true"

    last_status = 404
    last_text = ""

    async with httpx.AsyncClient(timeout=60.0) as client:
        for idx, cand in enumerate(candidates):
            enc_cand = urllib.parse.quote(cand, safe="/") if cand else ""
            url = (
                f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{enc_cand}"
                if enc_cand
                else f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files"
            )
            resp = await client.get(
                url,
                params=params,
                headers=build_headers(api_key),
            )
            if resp.status_code < 400:
                data = resp.json() if resp.content else {}
                files_norm = _extract_files_list_from_response(data)
                if files_norm or idx == 0:
                    if isinstance(data, dict):
                        data["files"] = files_norm
                        data["resolved_path"] = cand or "/"
                    return data
            else:
                last_status = resp.status_code
                last_text = resp.text
                if idx == 0 and clean_path not in ("workspace", "", "/"):
                    break

    raise ValueError(
        f"Erro ao listar arquivos do ambiente '{clean_id}' (HTTP {last_status}): {last_text}"
    )


def _normalize_env_target_path(raw_path: Optional[str], clean_id: str) -> str:
    if not raw_path:
        return ""
    p = raw_path.strip().replace("\\", "/")
    marker = f"environments/{clean_id}/files/"
    if marker in p:
        p = p.split(marker, 1)[1]
    elif "/files/" in p and p.startswith("environments/"):
        p = p.split("/files/", 1)[1]
    return p.lstrip("/")


def _extract_target_from_tar_bytes(
    tar_bytes: bytes,
    target_path: str,
) -> Optional[Tuple[bytes, str, str]]:
    """
    Se a API retornar um arquivo .tar / .tar.gz (seja do endpoint de arquivo ou do snapshot completo),
    procura o arquivo `target_path` dentro do pacote tar e devolve os bytes exatos do arquivo individual
    com seu nome e MIME type originais (ou um sub-tar.gz se for um diretório).
    """
    clean_target = target_path.strip("/")
    base_name = os.path.basename(clean_target) or "arquivo"
    wants_raw_tar = base_name.lower().endswith((".tar", ".tar.gz", ".tgz"))
    if wants_raw_tar:
        return None

    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            members = tar.getmembers()
            file_members = [m for m in members if m.isfile()]

            # 1. Se o tar contém apenas 1 arquivo regular e o usuário pediu um arquivo específico
            if len(file_members) == 1 and "." in base_name:
                m = file_members[0]
                extracted = tar.extractfile(m)
                if extracted is not None:
                    data = extracted.read()
                    mime = mimetypes.guess_type(base_name)[0] or "application/octet-stream"
                    return data, base_name, mime

            # 2. Procura correspondência exata ou pelo sufixo do caminho no tar
            candidates = [
                clean_target,
                clean_target.removeprefix("workspace/"),
                f"workspace/{clean_target}",
            ]
            for m in file_members:
                m_name = m.name.lstrip("./").lstrip("/")
                if m_name in candidates or m_name.endswith(f"/{clean_target}") or m_name == base_name:
                    extracted = tar.extractfile(m)
                    if extracted is not None:
                        data = extracted.read()
                        fname = os.path.basename(m_name) or base_name
                        mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                        return data, fname, mime

            # 3. Se for um diretório contendo múltiplos arquivos dentro do snapshot, re-empacota apenas a pasta
            dir_prefix_candidates = [f"{c.rstrip('/')}/" for c in candidates if c]
            matching_dir_members = [
                m for m in members
                if any(m.name.lstrip("./").lstrip("/").startswith(pref) for pref in dir_prefix_candidates)
            ]
            if matching_dir_members:
                out_buf = io.BytesIO()
                with tarfile.open(fileobj=out_buf, mode="w:gz") as out_tar:
                    for m in matching_dir_members:
                        if m.isfile():
                            fobj = tar.extractfile(m)
                            if fobj is not None:
                                data = fobj.read()
                                info = tarfile.TarInfo(name=m.name.lstrip("./").lstrip("/"))
                                info.size = len(data)
                                info.mode = m.mode or 0o644
                                out_tar.addfile(info, io.BytesIO(data))
                out_buf.seek(0)
                return out_buf.read(), f"{base_name or 'folder'}.tar.gz", "application/gzip"
    except Exception:
        return None

    return None


async def _build_tar_gz_from_environment_listing(
    client: httpx.AsyncClient,
    clean_id: str,
    base_path: str,
    listing_data: Dict[str, Any],
    headers: Dict[str, str],
) -> Tuple[bytes, str, str]:
    """
    Constrói um pacote `.tar.gz` em memória baixando todos os arquivos listados pela Environments API:
      1. Lista todos os arquivos recursivamente via `GET /v1beta/environments/{id}/files/{dir}?recursive=true`
      2. Baixa cada arquivo via `GET /v1beta/environments/{id}/files/{file_path}?alt=media`
      3. Compacta tudo em um arquivo `.tar.gz` mantendo a estrutura de diretórios original.
    Isso resolve definitivamente o erro HTTP 404 ('Directory not found or is empty') ao clicar em
    'Download Snapshot' ou ao baixar uma pasta da lista de arquivos!
    """
    import urllib.parse

    entries = _extract_files_list_from_response(listing_data)
    file_entries = [e for e in entries if e.get("type") != "directory" and e.get("path")]
    if not file_entries:
        raise ValueError(f"Nenhum arquivo encontrado em '{base_path or 'workspace'}' para compactar no snapshot.")

    sem = asyncio.Semaphore(6)

    async def _fetch_one(entry: Dict[str, Any]) -> Optional[Tuple[str, bytes]]:
        f_path = _normalize_env_target_path(str(entry.get("path") or ""), clean_id)
        if not f_path and base_path:
            f_path = f"{base_path.lstrip('/')}/{entry.get('name', '')}".lstrip("/")
        if not f_path:
            return None
        enc_f_path = urllib.parse.quote(f_path, safe="/")
        file_url = f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{enc_f_path}?alt=media"
        async with sem:
            try:
                r = await client.get(file_url, headers=headers)
                if r.status_code == 200 and r.content:
                    extracted = _extract_target_from_tar_bytes(r.content, f_path)
                    if extracted is not None:
                        return (f_path, extracted[0])
                    return (f_path, r.content)
            except Exception:
                pass
        return None

    results = await asyncio.gather(*[_fetch_one(e) for e in file_entries])
    downloaded = [item for item in results if item is not None]
    if not downloaded:
        raise ValueError(
            f"Não foi possível baixar o conteúdo dos arquivos listados em '{base_path or 'workspace'}' no ambiente '{clean_id}'."
        )

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel_p, data_bytes in downloaded:
            clean_rel = rel_p.lstrip("/")
            info = tarfile.TarInfo(name=clean_rel)
            info.size = len(data_bytes)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data_bytes))
    buf.seek(0)
    slug = (base_path or "workspace").strip("/").replace("/", "_") or "snapshot"
    return buf.read(), f"environment_snapshot_{clean_id}_{slug}.tar.gz", "application/gzip"


async def download_environment_snapshot(
    environment_id: str,
    path: Optional[str] = None,
    known_paths: Optional[List[str]] = None,
    api_key: Optional[str] = None,
) -> Tuple[bytes, str, str]:
    """
    Baixa o snapshot completo do ambiente remoto sandbox (.tar.gz) OU um arquivo/diretório específico
    selecionado na lista de arquivos do ambiente.
    - Quando `path` aponta para um arquivo individual (ex: `workspace/ID/relatorio.pdf`), baixa os bytes
      do arquivo via `GET /v1beta/environments/{id}/files/{path}?alt=media` e entrega com seu nome original.
    - Quando `path` é vazio (Download Snapshot) ou aponta para um diretório, tenta os endpoints de snapshot
      e, caso a API retorne HTTP 404 ('Directory not found or is empty') porque `?alt=media` na Environments API
      não suporta diretórios raiz diretamente, lista todos os arquivos recursivamente (`workspace`, pastas de
      upload da sessão, etc.) e monta o `.tar.gz` completo automaticamente!
    """
    import base64
    import urllib.parse

    clean_id = clean_environment_id(environment_id)
    if not clean_id or clean_id == "remote":
        raise ValueError("Informe um environment_id válido.")

    headers = build_headers(api_key)
    headers.pop("Content-Type", None)
    json_headers = build_headers(api_key)

    target_path = _normalize_env_target_path(path, clean_id)

    # Caso 1: Download do Snapshot Completo (sem path específico)
    if not target_path:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            # 1a. Tenta endpoint de snapshot na Files API
            snapshot_url = f"{FILES_BASE_URL}/environment-{clean_id}:download?alt=media"
            try:
                resp = await client.get(snapshot_url, headers=headers)
                if resp.status_code == 200 and resp.content:
                    ctype = resp.headers.get("content-type", "application/x-tar")
                    if "application/json" not in ctype.lower():
                        return resp.content, f"environment_snapshot_{clean_id}.tar.gz", ctype
            except Exception:
                pass

            # 1b. Tenta listar recursivamente os diretórios do ambiente (workspace + pastas de upload conhecidas)
            # e empacotar todos os arquivos do ambiente em um .tar.gz!
            candidate_dirs: List[str] = ["workspace"]
            if known_paths:
                for kp in known_paths:
                    ckp = _normalize_env_target_path(kp, clean_id)
                    if ckp and ckp not in candidate_dirs:
                        candidate_dirs.append(ckp)
                    top_seg = ckp.split("/")[0] if ckp else ""
                    if top_seg and top_seg not in candidate_dirs:
                        candidate_dirs.append(top_seg)
            for extra in ("", "."):
                if extra not in candidate_dirs:
                    candidate_dirs.append(extra)

            merged_entries_map: Dict[str, Dict[str, Any]] = {}
            last_err_detail = ""

            for cand_dir in candidate_dirs:
                enc_dir = urllib.parse.quote(cand_dir, safe="/") if cand_dir else ""
                list_url = (
                    f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{enc_dir}"
                    if enc_dir
                    else f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files"
                )
                try:
                    l_resp = await client.get(
                        list_url,
                        params={"recursive": "true"},
                        headers=json_headers,
                    )
                    if l_resp.status_code == 200 and l_resp.content:
                        jdata = l_resp.json()
                        for entry in _extract_files_list_from_response(jdata):
                            epath = entry.get("path") or ""
                            if epath and epath not in merged_entries_map:
                                merged_entries_map[epath] = entry
                        # Se já encontrou arquivos em "workspace" e não há outros known_paths fora de workspace, podemos parar
                        if merged_entries_map and cand_dir == "workspace" and not known_paths:
                            break
                    else:
                        last_err_detail = f"(HTTP {l_resp.status_code}): {l_resp.text}"
                except Exception as exc:
                    last_err_detail = str(exc)

            if merged_entries_map:
                return await _build_tar_gz_from_environment_listing(
                    client=client,
                    clean_id=clean_id,
                    base_path="workspace",
                    listing_data={"files": list(merged_entries_map.values())},
                    headers=headers,
                )

            # 1c. Último fallback: tenta GET /environments/{id}/files/workspace?alt=media ou raiz
            for fallback_url in (
                f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/workspace?alt=media",
                f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files?alt=media",
            ):
                resp = await client.get(fallback_url, headers=headers)
                if resp.status_code == 200 and resp.content:
                    ctype = resp.headers.get("content-type", "application/x-tar")
                    return resp.content, f"environment_snapshot_{clean_id}.tar.gz", ctype
                last_err_detail = f"(HTTP {resp.status_code}): {resp.text}"

            raise ValueError(
                f"Erro ao baixar snapshot do ambiente '{clean_id}' {last_err_detail}"
            )

    # Caso 2: Download de um Arquivo ou Diretório específico (target_path informado)
    encoded_path = urllib.parse.quote(target_path, safe="/")
    base_name = os.path.basename(target_path.rstrip("/")) or "arquivo"
    guessed_mime = mimetypes.guess_type(base_name)[0] or "application/octet-stream"

    candidate_urls = [
        f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{encoded_path}?alt=media",
        f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{encoded_path}:download?alt=media",
        f"https://generativelanguage.googleapis.com/download/v1beta/environments/{clean_id}/files/{encoded_path}?alt=media",
    ]

    last_status = 0
    last_text = ""

    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        for url in candidate_urls:
            try:
                resp = await client.get(url, headers=headers)
            except Exception as exc:
                last_text = str(exc)
                continue

            last_status = resp.status_code
            last_text = resp.text if resp.status_code >= 400 else ""

            if resp.status_code == 200 and resp.content:
                resp_ctype = (resp.headers.get("content-type") or "").lower()

                # Se a API retornou JSON (ex: metadados com content/data em base64 ou listagem de diretório)
                if "application/json" in resp_ctype and not base_name.lower().endswith(".json"):
                    try:
                        jdata = resp.json()
                        if isinstance(jdata, dict):
                            if "content" in jdata or "data" in jdata:
                                raw_val = jdata.get("content") or jdata.get("data") or ""
                                try:
                                    decoded = base64.b64decode(raw_val)
                                    return decoded, base_name, guessed_mime
                                except Exception:
                                    return str(raw_val).encode("utf-8"), base_name, guessed_mime
                            if "files" in jdata or "entries" in jdata:
                                return await _build_tar_gz_from_environment_listing(
                                    client=client,
                                    clean_id=clean_id,
                                    base_path=target_path,
                                    listing_data=jdata,
                                    headers=headers,
                                )
                    except Exception:
                        pass

                # Verifica se a resposta veio encapsulada em um pacote .tar / .tar.gz
                extracted = _extract_target_from_tar_bytes(resp.content, target_path)
                if extracted is not None:
                    return extracted

                if ("tar" in resp_ctype or "gzip" in resp_ctype) and not base_name.lower().endswith((".tar", ".tar.gz", ".tgz")):
                    return resp.content, f"{base_name}.tar.gz", resp_ctype

                final_mime = guessed_mime if guessed_mime != "application/octet-stream" else (resp_ctype or "application/octet-stream")
                return resp.content, base_name, final_mime

        # Se ?alt=media retornou 400/404 porque `target_path` é uma pasta (ex: "workspace/ID"),
        # consulta a listagem recursiva desse diretório e gera um .tar.gz com todos os seus arquivos!
        try:
            dir_list_resp = await client.get(
                f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{encoded_path}",
                params={"recursive": "true"},
                headers=json_headers,
            )
            if dir_list_resp.status_code == 200 and dir_list_resp.content:
                jdata = dir_list_resp.json()
                entries = _extract_files_list_from_response(jdata)
                if entries:
                    return await _build_tar_gz_from_environment_listing(
                        client=client,
                        clean_id=clean_id,
                        base_path=target_path,
                        listing_data=jdata,
                        headers=headers,
                    )
        except Exception:
            pass

        # Fallback: tenta sem o prefixo "workspace/" ou adicionando "workspace/" caso o caminho relativo varie
        alt_target = (
            target_path.removeprefix("workspace/")
            if target_path.startswith("workspace/")
            else f"workspace/{target_path}"
        )
        enc_alt = urllib.parse.quote(alt_target, safe="/")
        try:
            alt_resp = await client.get(
                f"{ENVIRONMENTS_BASE_URL}/{clean_id}/files/{enc_alt}?alt=media",
                headers=headers,
            )
            if alt_resp.status_code == 200 and alt_resp.content:
                extracted = _extract_target_from_tar_bytes(alt_resp.content, alt_target)
                if extracted is not None:
                    return extracted
                return alt_resp.content, base_name, guessed_mime
        except Exception:
            pass

    raise ValueError(
        f"Erro ao baixar '{target_path}' do ambiente '{clean_id}' (HTTP {last_status}): {last_text}"
    )


