package com.antigravity.studio.data.remote

import android.webkit.MimeTypeMap
import com.antigravity.studio.data.model.EnvFileEntry
import com.antigravity.studio.data.model.SessionModel
import com.antigravity.studio.data.model.SourceItem
import com.antigravity.studio.data.model.UploadedFileRecord
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.apache.commons.compress.archivers.tar.TarArchiveEntry
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream
import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream
import org.apache.commons.compress.compressors.gzip.GzipCompressorOutputStream
import org.json.JSONArray
import org.json.JSONObject
import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.BufferedInputStream
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import java.util.zip.ZipInputStream

data class DownloadedResource(
    val bytes: ByteArray,
    val fileName: String,
    val mimeType: String
)

object InteractionsClient {

    const val INTERACTIONS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
    const val ENVIRONMENTS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/environments"
    const val ENVIRONMENTS_UPLOAD_BASE_URL = "https://generativelanguage.googleapis.com/upload/v1beta/environments"
    const val FILES_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/files"

    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(60, TimeUnit.SECONDS)
            .readTimeout(300, TimeUnit.SECONDS)
            .writeTimeout(300, TimeUnit.SECONDS)
            .followRedirects(true)
            .followSslRedirects(true)
            .build()
    }

    private const val SANDBOX_DOC_EXTRACTOR_SCRIPT = """#!/usr/bin/env python3
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
            elif low.endswith(".pdf") and not os.path.exists(out_p):
                txt = extract_pdf(p)
                open(out_p, "w", encoding="utf-8").write(txt)
        except Exception as e:
            print(f"Erro em {p}: {e}")
"""

    fun cleanEnvironmentId(environmentId: String?): String {
        val raw = (environmentId ?: "").trim()
        return if (raw.startsWith("environments/")) {
            raw.removePrefix("environments/")
        } else {
            raw
        }
    }

    fun extractTextsFromInteraction(
        data: JSONObject,
        fallbackUserInput: String = ""
    ): Pair<String, String> {
        val userTexts = mutableListOf<String>()
        val outputTexts = mutableListOf<String>()

        val rawInput = data.opt("input")
        when (rawInput) {
            is String -> if (rawInput.isNotBlank()) userTexts.add(rawInput.trim())
            is JSONArray -> {
                for (i in 0 until rawInput.length()) {
                    val item = rawInput.optJSONObject(i) ?: continue
                    if (item.optString("type") == "text") {
                        userTexts.add(item.optString("text", ""))
                    } else if (item.optString("type") == "user_input" || item.has("content")) {
                        val contentArr = item.optJSONArray("content")
                        if (contentArr != null) {
                            for (j in 0 until contentArr.length()) {
                                val c = contentArr.optJSONObject(j) ?: continue
                                if (c.optString("type") == "text") {
                                    userTexts.add(c.optString("text", ""))
                                }
                            }
                        }
                    }
                }
            }
        }

        val steps = data.optJSONArray("steps")
        if (steps != null) {
            for (i in 0 until steps.length()) {
                val step = steps.optJSONObject(i) ?: continue
                val stype = step.optString("type", "")
                val contents = step.optJSONArray("content") ?: continue
                if (stype == "user_input" && userTexts.isEmpty()) {
                    for (j in 0 until contents.length()) {
                        val c = contents.optJSONObject(j) ?: continue
                        if (c.optString("type") == "text") {
                            userTexts.add(c.optString("text", ""))
                        }
                    }
                } else if (stype == "model_output") {
                    for (j in 0 until contents.length()) {
                        val c = contents.optJSONObject(j) ?: continue
                        if (c.optString("type") == "text") {
                            outputTexts.add(c.optString("text", ""))
                        }
                    }
                }
            }
        }

        if (outputTexts.isEmpty() && data.has("output_text")) {
            val outTxt = data.optString("output_text", "")
            if (outTxt.isNotBlank()) outputTexts.add(outTxt)
        }

        var userInputStr = userTexts.filter { it.isNotBlank() }.joinToString("\n").trim()
            .ifBlank { fallbackUserInput }
        val marker = "\n\n---\n[NOTA TÉCNICA DO AMBIENTE SANDBOX"
        if (userInputStr.contains(marker)) {
            userInputStr = userInputStr.substringBefore(marker).trim()
        }
        val historyMarker = "[NOVA INSTRUÇÃO DO USUÁRIO]:\n"
        if (userInputStr.contains(historyMarker)) {
            userInputStr = userInputStr.substringAfter(historyMarker).trim()
        }

        val outputStr = outputTexts.filter { it.isNotBlank() }.joinToString("\n\n").trim()
        return userInputStr to outputStr
    }

    fun enrichAgentPromptForSandbox(
        prompt: String,
        uploadedFiles: List<UploadedFileRecord> = emptyList()
    ): String {
        if (prompt.trim().equals("continue", ignoreCase = true)) return prompt

        val docRegex = Regex("\\.(pdf|odt|odp|ods|docx|pptx|xlsx)\\b", RegexOption.IGNORE_CASE)
        val hasDocExt = docRegex.containsMatchIn(prompt)
        val hasUploads = uploadedFiles.isNotEmpty()
        if (!hasDocExt && !hasUploads) return prompt

        val notes = mutableListOf(
            "\n\n---",
            "[NOTA TÉCNICA DO AMBIENTE SANDBOX (ANTIGRAVITY STUDIO)]:"
        )
        if (hasUploads) {
            val paths = uploadedFiles.mapNotNull { it.targetPath.takeIf { p -> p.isNotBlank() } }.distinct()
            if (paths.isNotEmpty()) {
                val formatted = paths.joinToString(", ") { "`$it`" }
                notes.add("- Arquivos/pastas enviados via Environments API estão localizados em: $formatted (use `find_by_name` ou `execute_bash` com `ls -la` para listar os nomes exatos).")
            }
        }
        notes.addAll(
            listOf(
                "- REGRA CRÍTICA (PREVENÇÃO DE ERRO HTTP 500 NA API): NUNCA use a ferramenta `view_file` diretamente em arquivos `.pdf`, `.odt`, `.odp`, `.ods`, `.docx`, `.pptx` ou `.xlsx`.",
                "- COMO LER ESSES DOCUMENTOS COM SEGURANÇA:",
                "  1. Verifique primeiro se já existem arquivos `.extracted.txt` gerados automaticamente ao lado dos documentos ou o script `_extract_docs_helper.py`.",
                "  2. Caso precise extrair texto de `.odt` ou `.odp`, use `execute_bash` com `python3` lendo `content.xml` via `zipfile.ZipFile` e `xml.etree.ElementTree`.",
                "  3. Caso precise extrair texto de `.pdf`, use `execute_bash` com `python3` (`pypdf` ou `pymupdf`) salvando o texto em `/tmp/*.txt`."
            )
        )
        return prompt + notes.joinToString("\n")
    }

    fun buildCreatePayload(
        prompt: String,
        mode: String = "agent",
        agentName: String = "antigravity-preview-09-2026",
        modelName: String = "gemini-3.8-flash",
        previousInteractionId: String? = null,
        environmentId: String? = null,
        maxTotalTokens: Int = 0,
        systemInstruction: String? = null,
        background: Boolean = false,
        sources: List<SourceItem> = emptyList(),
        uploadedFiles: List<UploadedFileRecord> = emptyList()
    ): JSONObject {
        val effectivePrompt = if (mode == "agent") {
            enrichAgentPromptForSandbox(prompt, uploadedFiles)
        } else {
            prompt
        }

        val payload = JSONObject().apply {
            put("input", effectivePrompt)
            put("store", true)
        }

        if (!previousInteractionId.isNullOrBlank()) {
            payload.put("previous_interaction_id", previousInteractionId.trim())
        }
        if (background) {
            payload.put("background", true)
        }

        if (mode == "agent") {
            val effAgent = agentName.ifBlank { "antigravity-preview-09-2026" }
            payload.put("agent", effAgent)
            val envVal = cleanEnvironmentId(environmentId)

            if ((envVal.isBlank() || envVal == "remote") && sources.isNotEmpty()) {
                val cleanSources = JSONArray()
                for (s in sources) {
                    when (s.type) {
                        "repository" -> if (s.source.isNotBlank()) {
                            cleanSources.put(
                                JSONObject()
                                    .put("type", "repository")
                                    .put("source", s.source.trim())
                                    .put("target", s.target.ifBlank { "/workspace" }.trim())
                            )
                        }
                        "gcs" -> if (s.source.isNotBlank()) {
                            cleanSources.put(
                                JSONObject()
                                    .put("type", "gcs")
                                    .put("source", s.source.trim())
                                    .put("target", s.target.ifBlank { "/workspace" }.trim())
                            )
                        }
                        "inline" -> if (s.target.isNotBlank()) {
                            cleanSources.put(
                                JSONObject()
                                    .put("type", "inline")
                                    .put("content", s.content)
                                    .put("target", s.target.trim())
                            )
                        }
                    }
                }
                if (cleanSources.length() > 0) {
                    payload.put(
                        "environment",
                        JSONObject()
                            .put("type", "remote")
                            .put("sources", cleanSources)
                    )
                } else {
                    payload.put("environment", "remote")
                }
            } else {
                payload.put("environment", envVal.ifBlank { "remote" })
            }

            if (effAgent.contains("antigravity")) {
                val agentCfg = JSONObject()
                    .put("type", "antigravity")
                    .put("model", modelName.ifBlank { "gemini-3.8-flash" })
                if (maxTotalTokens > 0) {
                    agentCfg.put("max_total_tokens", maxTotalTokens)
                }
                payload.put("agent_config", agentCfg)
            }
        } else {
            payload.put("model", modelName.ifBlank { "gemini-3.8-flash" })
            if (!systemInstruction.isNullOrBlank()) {
                payload.put("system_instruction", systemInstruction.trim())
            }
        }

        return payload
    }

    suspend fun getInteraction(
        interactionId: String,
        includeInput: Boolean = true,
        apiKey: String
    ): JSONObject = withContext(Dispatchers.IO) {
        if (apiKey.isBlank()) {
            throw IllegalArgumentException("Configure sua Gemini API Key antes de consultar a API.")
        }
        var currentIncludeInput = includeInput
        for (attempt in 0 until 3) {
            val urlBuilder = "$INTERACTIONS_BASE_URL/${interactionId.trim()}".toHttpUrl().newBuilder()
            if (currentIncludeInput) {
                urlBuilder.addQueryParameter("include_input", "true")
            }
            val request = Request.Builder()
                .url(urlBuilder.build())
                .get()
                .addHeader("Content-Type", "application/json")
                .addHeader("x-goog-api-key", apiKey.trim())
                .build()

            try {
                httpClient.newCall(request).execute().use { resp ->
                    val bodyStr = resp.body?.string() ?: "{}"
                    if (resp.code in listOf(500, 502, 503, 504) && attempt < 2) {
                        if (currentIncludeInput && attempt == 1) {
                            currentIncludeInput = false
                        }
                        delay(1500L * (attempt + 1))
                        return@use
                    }
                    if (resp.code >= 400) {
                        throw IllegalStateException("Erro ao buscar interação '$interactionId' (HTTP ${resp.code}): $bodyStr")
                    }
                    return@withContext JSONObject(bodyStr)
                }
            } catch (e: IOException) {
                if (attempt < 2) {
                    delay(1500L * (attempt + 1))
                    continue
                }
                throw IllegalStateException("Timeout ou erro de rede ao consultar interação '$interactionId': ${e.message}")
            }
        }
        throw IllegalStateException("Erro inesperado ao buscar interação '$interactionId'.")
    }

    suspend fun recoverConversationChain(
        targetInteractionId: String,
        apiKey: String,
        maxDepth: Int = 100
    ): List<JSONObject> = withContext(Dispatchers.IO) {
        if (apiKey.isBlank()) {
            throw IllegalArgumentException("Configure sua Gemini API Key antes de importar uma conversa.")
        }
        val chain = mutableListOf<JSONObject>()
        val seenIds = mutableSetOf<String>()
        var currentId: String? = targetInteractionId.trim()

        while (!currentId.isNullOrBlank() && chain.size < maxDepth) {
            if (!seenIds.add(currentId)) break

            val url = "$INTERACTIONS_BASE_URL/$currentId".toHttpUrl().newBuilder()
                .addQueryParameter("include_input", "true")
                .build()

            val request = Request.Builder()
                .url(url)
                .get()
                .addHeader("Content-Type", "application/json")
                .addHeader("x-goog-api-key", apiKey.trim())
                .build()

            httpClient.newCall(request).execute().use { resp ->
                val bodyStr = resp.body?.string() ?: "{}"
                if (resp.code >= 400) {
                    if (chain.isEmpty()) {
                        throw IllegalStateException(
                            "Não foi possível recuperar a interação '$currentId' (HTTP ${resp.code}): $bodyStr"
                        )
                    }
                    currentId = null
                    return@use
                }
                val interactionData = JSONObject(bodyStr)
                chain.add(interactionData)
                val prevId = interactionData.optString("previous_interaction_id", "").trim()
                currentId = prevId.ifBlank { null }
            }
        }
        chain.reverse()
        chain
    }

    suspend fun createInteraction(
        payload: JSONObject,
        apiKey: String,
        fallbackHistoryText: String? = null
    ): JSONObject = withContext(Dispatchers.IO) {
        if (apiKey.isBlank()) {
            throw IllegalArgumentException("Configure sua Gemini API Key no botão 🔑 API Key.")
        }
        val currentPayload = JSONObject(payload.toString())
        val isAgent = currentPayload.has("agent")
        var droppedPrevId: String? = null

        for (attempt in 0 until 4) {
            val reqBody = currentPayload.toString()
                .toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
            val request = Request.Builder()
                .url(INTERACTIONS_BASE_URL)
                .post(reqBody)
                .addHeader("Content-Type", "application/json")
                .addHeader("x-goog-api-key", apiKey.trim())
                .build()

            try {
                httpClient.newCall(request).execute().use { resp ->
                    val bodyStr = resp.body?.string() ?: "{}"
                    if (resp.code < 400) {
                        val data = JSONObject(bodyStr)
                        if (droppedPrevId != null) {
                            data.put("_dropped_previous_interaction_id", droppedPrevId)
                        }
                        return@withContext data
                    }

                    // Se previous_interaction_id falhou com 400 ou 404, desvincula mantendo o mesmo environment_id
                    if (resp.code in listOf(400, 404) && currentPayload.has("previous_interaction_id") && attempt < 3) {
                        droppedPrevId = currentPayload.optString("previous_interaction_id")
                        currentPayload.remove("previous_interaction_id")
                        if (!fallbackHistoryText.isNullOrBlank()) {
                            val origInput = currentPayload.optString("input", "")
                            currentPayload.put(
                                "input",
                                "[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n$fallbackHistoryText\n\n[NOVA INSTRUÇÃO DO USUÁRIO]:\n$origInput"
                            )
                        }
                        delay(800L)
                        return@use
                    }

                    // Tratamento resiliente para HTTP 500 / 502 / 503 / 504
                    if (resp.code in listOf(500, 502, 503, 504) && attempt < 3) {
                        if (isAgent && !currentPayload.optBoolean("background", false)) {
                            currentPayload.put("background", true)
                            delay(1000L)
                            return@use
                        }
                        if (currentPayload.has("previous_interaction_id")) {
                            droppedPrevId = currentPayload.optString("previous_interaction_id")
                            currentPayload.remove("previous_interaction_id")
                            if (!fallbackHistoryText.isNullOrBlank()) {
                                val origInput = currentPayload.optString("input", "")
                                currentPayload.put(
                                    "input",
                                    "[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n$fallbackHistoryText\n\n[NOVA INSTRUÇÃO DO USUÁRIO]:\n$origInput"
                                )
                            }
                            delay(1000L)
                            return@use
                        }
                        delay(1500L * (attempt + 1))
                        return@use
                    }

                    throw IllegalStateException("Erro na Interactions API (HTTP ${resp.code}): $bodyStr")
                }
            } catch (e: IOException) {
                if (isAgent && !currentPayload.optBoolean("background", false) && attempt < 3) {
                    currentPayload.put("background", true)
                    continue
                }
                if (attempt < 3) {
                    delay(1500L * (attempt + 1))
                    continue
                }
                throw IllegalStateException("Timeout ou falha de conexão com a Interactions API. Ative 'Execução em Background' para tarefas longas.")
            }
        }
        throw IllegalStateException("Erro inesperado ao criar interação.")
    }

    suspend fun cancelInteraction(
        interactionId: String,
        apiKey: String
    ): JSONObject = withContext(Dispatchers.IO) {
        val cleanId = interactionId.trim()
        if (cleanId.isBlank()) throw IllegalArgumentException("O interaction_id é obrigatório.")

        val attempts = listOf(
            Triple("$INTERACTIONS_BASE_URL/$cleanId/cancel", false, ByteArray(0)),
            Triple("$INTERACTIONS_BASE_URL/$cleanId/cancel", true, "{}".toByteArray()),
            Triple("$INTERACTIONS_BASE_URL/$cleanId:cancel", false, ByteArray(0)),
            Triple("$INTERACTIONS_BASE_URL/$cleanId:cancel", true, "{}".toByteArray())
        )

        var lastStatus = 0
        var lastText = ""

        for ((url, isJson, bytes) in attempts) {
            try {
                val reqBuilder = Request.Builder()
                    .url(url)
                    .post(bytes.toRequestBody(if (isJson) "application/json".toMediaTypeOrNull() else null))
                    .addHeader("Accept", "application/json")
                    .addHeader("x-goog-api-key", apiKey.trim())

                httpClient.newCall(reqBuilder.build()).execute().use { resp ->
                    val bodyStr = resp.body?.string() ?: ""
                    if (resp.code < 400) {
                        if (bodyStr.isBlank()) {
                            return@withContext JSONObject().put("id", cleanId).put("status", "cancelled")
                        }
                        return@withContext try {
                            val obj = JSONObject(bodyStr)
                            if (!obj.has("status")) obj.put("status", "cancelled")
                            obj
                        } catch (_: Exception) {
                            JSONObject().put("id", cleanId).put("status", "cancelled")
                        }
                    }
                    lastStatus = resp.code
                    lastText = bodyStr

                    if (resp.code in listOf(400, 409, 412)) {
                        try {
                            val state = getInteraction(cleanId, includeInput = true, apiKey = apiKey)
                            val curStatus = state.optString("status", "")
                            if (curStatus.isNotBlank() && curStatus != "in_progress") {
                                return@withContext state
                            }
                        } catch (_: Exception) {
                        }
                    }
                }
            } catch (e: Exception) {
                lastText = e.message ?: ""
            }
        }

        try {
            val state = getInteraction(cleanId, includeInput = true, apiKey = apiKey)
            state.put("status", "cancelled")
            state.put("_locally_cancelled", true)
            return@withContext state
        } catch (_: Exception) {
            throw IllegalStateException("Erro ao cancelar interação '$cleanId' (HTTP $lastStatus): $lastText")
        }
    }

    suspend fun createRemoteEnvironment(
        sources: List<SourceItem> = emptyList(),
        fromEnvironment: String? = null,
        apiKey: String
    ): JSONObject = withContext(Dispatchers.IO) {
        if (apiKey.isBlank()) {
            throw IllegalArgumentException("Configure sua Gemini API Key antes de provisionar um ambiente.")
        }
        val body = JSONObject()
        if (!fromEnvironment.isNullOrBlank()) {
            val cleanFrom = cleanEnvironmentId(fromEnvironment)
            body.put("from_environment", "environments/$cleanFrom")
        } else if (sources.isNotEmpty()) {
            val cleanSources = JSONArray()
            for (s in sources) {
                when (s.type) {
                    "repository" -> if (s.source.isNotBlank()) {
                        cleanSources.put(
                            JSONObject()
                                .put("type", "repository")
                                .put("source", s.source.trim())
                                .put("target", s.target.ifBlank { "/workspace" }.trim())
                        )
                    }
                    "gcs" -> if (s.source.isNotBlank()) {
                        cleanSources.put(
                            JSONObject()
                                .put("type", "gcs")
                                .put("source", s.source.trim())
                                .put("target", s.target.ifBlank { "/workspace" }.trim())
                        )
                    }
                    "inline" -> if (s.target.isNotBlank()) {
                        cleanSources.put(
                            JSONObject()
                                .put("type", "inline")
                                .put("content", s.content)
                                .put("target", s.target.trim())
                        )
                    }
                }
            }
            if (cleanSources.length() > 0) {
                body.put("sources", cleanSources)
            }
        }

        val request = Request.Builder()
            .url(ENVIRONMENTS_BASE_URL)
            .post(body.toString().toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull()))
            .addHeader("Content-Type", "application/json")
            .addHeader("x-goog-api-key", apiKey.trim())
            .build()

        httpClient.newCall(request).execute().use { resp ->
            val bodyStr = resp.body?.string() ?: "{}"
            if (resp.code >= 400) {
                throw IllegalStateException("Erro ao provisionar ambiente na Environments API (HTTP ${resp.code}): $bodyStr")
            }
            val data = JSONObject(bodyStr)
            val envId = data.optString("id").ifBlank { data.optString("name") }
            if (envId.isNotBlank()) {
                data.put("id", cleanEnvironmentId(envId))
            }
            data
        }
    }

    fun extractTextFromDocumentBytes(filename: String, content: ByteArray): String? {
        val lower = filename.lowercase()
        return try {
            when {
                lower.endsWith(".odt") || lower.endsWith(".odp") || lower.endsWith(".ods") -> {
                    extractXmlFromZip(content) { entryName -> entryName == "content.xml" }
                }
                lower.endsWith(".docx") -> {
                    extractXmlFromZip(content) { entryName -> entryName == "word/document.xml" }
                }
                lower.endsWith(".pptx") -> {
                    extractXmlFromZip(content) { entryName ->
                        entryName.startsWith("ppt/slides/slide") && entryName.endsWith(".xml")
                    }
                }
                else -> null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun extractXmlFromZip(
        zipBytes: ByteArray,
        entryPredicate: (String) -> Boolean
    ): String? {
        val chunks = mutableListOf<String>()
        ZipInputStream(ByteArrayInputStream(zipBytes)).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                if (!entry.isDirectory && entryPredicate(entry.name)) {
                    val xmlBytes = zis.readBytes()
                    val parsed = parseAllTextFromXml(xmlBytes)
                    if (parsed.isNotBlank()) {
                        chunks.add(parsed)
                    }
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
        return chunks.joinToString("\n\n").takeIf { it.isNotBlank() }
    }

    private fun parseAllTextFromXml(xmlBytes: ByteArray): String {
        val factory = XmlPullParserFactory.newInstance().apply { isNamespaceAware = true }
        val parser = factory.newPullParser()
        parser.setInput(ByteArrayInputStream(xmlBytes), "UTF-8")
        val sb = StringBuilder()
        var event = parser.eventType
        while (event != XmlPullParser.END_DOCUMENT) {
            when (event) {
                XmlPullParser.TEXT -> {
                    val t = parser.text?.trim()
                    if (!t.isNullOrEmpty()) {
                        sb.append(t).append(" ")
                    }
                }
                XmlPullParser.END_TAG -> {
                    val name = parser.name ?: ""
                    if (name in listOf("p", "h", "list-item", "table-cell")) {
                        sb.append("\n")
                    }
                }
            }
            event = parser.next()
        }
        return sb.toString().lines().map { it.trim() }.filter { it.isNotBlank() }.joinToString("\n")
    }

    fun packFilesAsTarGz(filesList: List<Pair<String, ByteArray>>): ByteArray {
        val baos = ByteArrayOutputStream()
        var hasComplexDocs = false

        GzipCompressorOutputStream(baos).use { gzos ->
            TarArchiveOutputStream(gzos).use { tar ->
                tar.setLongFileMode(TarArchiveOutputStream.LONGFILE_POSIX)
                for ((relPath, content) in filesList) {
                    val cleanRel = relPath.replace("\\", "/").trimStart('/').ifBlank { "arquivo" }
                    val entry = TarArchiveEntry(cleanRel).apply {
                        size = content.size.toLong()
                        mode = 420 // 0644
                    }
                    tar.putArchiveEntry(entry)
                    tar.write(content)
                    tar.closeArchiveEntry()

                    if (cleanRel.lowercase().let {
                            it.endsWith(".pdf") || it.endsWith(".odt") || it.endsWith(".odp") ||
                                it.endsWith(".ods") || it.endsWith(".docx") || it.endsWith(".pptx")
                        }
                    ) {
                        hasComplexDocs = true
                        val extractedText = extractTextFromDocumentBytes(cleanRel, content)
                        if (!extractedText.isNullOrBlank()) {
                            val txtBytes = extractedText.toByteArray(Charsets.UTF_8)
                            val txtEntry = TarArchiveEntry("$cleanRel.extracted.txt").apply {
                                size = txtBytes.size.toLong()
                                mode = 420
                            }
                            tar.putArchiveEntry(txtEntry)
                            tar.write(txtBytes)
                            tar.closeArchiveEntry()
                        }
                    }
                }

                if (hasComplexDocs) {
                    val helperBytes = SANDBOX_DOC_EXTRACTOR_SCRIPT.toByteArray(Charsets.UTF_8)
                    val helperEntry = TarArchiveEntry("_extract_docs_helper.py").apply {
                        size = helperBytes.size.toLong()
                        mode = 493 // 0755
                    }
                    tar.putArchiveEntry(helperEntry)
                    tar.write(helperBytes)
                    tar.closeArchiveEntry()
                }
            }
        }
        return baos.toByteArray()
    }

    suspend fun uploadFileToEnvironment(
        environmentId: String,
        targetPath: String,
        fileBytes: ByteArray,
        mimeType: String? = null,
        overwrite: Boolean = true,
        extract: Boolean = false,
        resumable: Boolean = false,
        apiKey: String
    ): JSONObject = withContext(Dispatchers.IO) {
        val cleanId = cleanEnvironmentId(environmentId)
        if (cleanId.isBlank() || cleanId == "remote") {
            throw IllegalArgumentException("É necessário um environment_id válido (ex: env_...) para fazer upload.")
        }
        val cleanPath = targetPath.trim().trimStart('/')
        if (cleanPath.isBlank()) {
            throw IllegalArgumentException("O caminho de destino (path) dentro do ambiente não pode ser vazio ou '/'.")
        }

        val resolvedMime = when {
            !mimeType.isNullOrBlank() -> mimeType
            extract || cleanPath.endsWith(".tar.gz") || cleanPath.endsWith(".tgz") || cleanPath.endsWith(".tar") -> "application/x-tar"
            else -> guessMimeType(cleanPath)
        }

        val urlBuilder = "$ENVIRONMENTS_UPLOAD_BASE_URL/$cleanId/files/$cleanPath".toHttpUrl().newBuilder()
        urlBuilder.addQueryParameter("overwrite", if (overwrite) "true" else "false")
        if (extract) {
            urlBuilder.addQueryParameter("extract", "true")
        }

        if (resumable) {
            val resumableUrl = urlBuilder.addQueryParameter("uploadType", "resumable").build()
            val initReq = Request.Builder()
                .url(resumableUrl)
                .put(ByteArray(0).toRequestBody(null))
                .addHeader("x-goog-api-key", apiKey.trim())
                .addHeader("X-Upload-Content-Type", resolvedMime)
                .addHeader("X-Upload-Content-Length", fileBytes.size.toString())
                .addHeader("X-Goog-Upload-Protocol", "resumable")
                .addHeader("X-Goog-Upload-Command", "start")
                .addHeader("X-Goog-Upload-Header-Content-Length", fileBytes.size.toString())
                .addHeader("X-Goog-Upload-Header-Content-Type", resolvedMime)
                .build()

            httpClient.newCall(initReq).execute().use { initResp ->
                if (initResp.code >= 400) {
                    throw IllegalStateException("Erro ao iniciar upload resumível (HTTP ${initResp.code}): ${initResp.body?.string()}")
                }
                val scottyUrl = initResp.header("x-goog-upload-url")
                val locationUrl = initResp.header("location")
                val sessionUrl = scottyUrl ?: locationUrl
                if (!sessionUrl.isNullOrBlank()) {
                    val finalizeReq = Request.Builder()
                        .url(sessionUrl)
                        .post(fileBytes.toRequestBody(resolvedMime.toMediaTypeOrNull()))
                        .addHeader("x-goog-api-key", apiKey.trim())
                        .addHeader("X-Goog-Upload-Command", "upload, finalize")
                        .addHeader("X-Goog-Upload-Offset", "0")
                        .addHeader("Content-Type", resolvedMime)
                        .build()
                    httpClient.newCall(finalizeReq).execute().use { finResp ->
                        val bodyStr = finResp.body?.string() ?: "{}"
                        if (finResp.code >= 400) {
                            throw IllegalStateException("Erro ao finalizar upload resumível (HTTP ${finResp.code}): $bodyStr")
                        }
                        return@withContext JSONObject()
                            .put("environment_id", cleanId)
                            .put("target_path", "/$cleanPath")
                            .put("size_bytes", fileBytes.size)
                            .put("mime_type", resolvedMime)
                            .put("extract", extract)
                            .put("overwrite", overwrite)
                            .put("upload_mode", "resumable")
                    }
                }
            }
        }

        // Upload direto via HTTP PUT
        val directReq = Request.Builder()
            .url(urlBuilder.build())
            .put(fileBytes.toRequestBody(resolvedMime.toMediaTypeOrNull()))
            .addHeader("x-goog-api-key", apiKey.trim())
            .addHeader("Content-Type", resolvedMime)
            .build()

        httpClient.newCall(directReq).execute().use { resp ->
            val scottyUrl = resp.header("x-goog-upload-url")
            if (resp.code == 200 && !scottyUrl.isNullOrBlank()) {
                val finalizeReq = Request.Builder()
                    .url(scottyUrl)
                    .post(fileBytes.toRequestBody(resolvedMime.toMediaTypeOrNull()))
                    .addHeader("x-goog-api-key", apiKey.trim())
                    .addHeader("X-Goog-Upload-Command", "upload, finalize")
                    .addHeader("X-Goog-Upload-Offset", "0")
                    .addHeader("Content-Type", resolvedMime)
                    .build()
                httpClient.newCall(finalizeReq).execute().use { finResp ->
                    val finBody = finResp.body?.string() ?: "{}"
                    if (finResp.code >= 400) {
                        throw IllegalStateException("Erro ao finalizar upload Scotty (HTTP ${finResp.code}): $finBody")
                    }
                    return@withContext JSONObject()
                        .put("environment_id", cleanId)
                        .put("target_path", "/$cleanPath")
                        .put("size_bytes", fileBytes.size)
                        .put("mime_type", resolvedMime)
                        .put("extract", extract)
                        .put("overwrite", overwrite)
                        .put("upload_mode", "scotty_resumable")
                }
            }

            val bodyStr = resp.body?.string() ?: "{}"
            if (resp.code >= 400) {
                throw IllegalStateException("Erro ao fazer upload para '$cleanId' em '$cleanPath' (HTTP ${resp.code}): $bodyStr")
            }
            JSONObject()
                .put("environment_id", cleanId)
                .put("target_path", "/$cleanPath")
                .put("size_bytes", fileBytes.size)
                .put("mime_type", resolvedMime)
                .put("extract", extract)
                .put("overwrite", overwrite)
                .put("upload_mode", "direct_put")
        }
    }

    private fun extractFilesListFromResponse(rawData: JSONObject): List<EnvFileEntry> {
        val rawArr = rawData.optJSONArray("files") ?: rawData.optJSONArray("entries") ?: return emptyList()
        val list = mutableListOf<EnvFileEntry>()
        for (i in 0 until rawArr.length()) {
            val item = rawArr.opt(i)
            when (item) {
                is String -> {
                    val cleanP = item.trimStart('/')
                    list.add(
                        EnvFileEntry(
                            name = cleanP.substringAfterLast('/').ifBlank { cleanP },
                            path = cleanP,
                            type = "file"
                        )
                    )
                }
                is JSONObject -> {
                    val rawP = item.optString("path", item.optString("name", "")).trimStart('/')
                    val rawType = item.optString("type", "").lowercase()
                    val mime = item.optString("mime_type", item.optString("mimeType", ""))
                    val isDir = rawType in listOf("directory", "dir", "folder") ||
                        item.optBoolean("is_dir", false) ||
                        mime == "inode/directory"
                    val sizeVal = item.optLong("size_bytes", item.optLong("sizeBytes", item.optLong("size", 0L)))
                    list.add(
                        EnvFileEntry(
                            name = item.optString("name").ifBlank { rawP.trimEnd('/').substringAfterLast('/').ifBlank { rawP } },
                            path = rawP,
                            sizeBytes = sizeVal,
                            mimeType = mime,
                            type = if (isDir) "directory" else "file",
                            modified = item.optString("modified", item.optString("updated", ""))
                        )
                    )
                }
            }
        }
        return list
    }

    suspend fun listEnvironmentFiles(
        environmentId: String,
        path: String = "workspace",
        recursive: Boolean = true,
        knownPaths: List<String> = emptyList(),
        apiKey: String
    ): Pair<String, List<EnvFileEntry>> = withContext(Dispatchers.IO) {
        val cleanId = cleanEnvironmentId(environmentId)
        if (cleanId.isBlank() || cleanId == "remote") {
            throw IllegalArgumentException("Informe um environment_id válido (ex: env_...).")
        }

        val cleanPath = path.trim().trimStart('/')
        val candidates = mutableListOf(cleanPath)
        for (kp in knownPaths) {
            val ckp = kp.trim().trimStart('/')
            if (ckp.isNotBlank() && ckp !in candidates) candidates.add(ckp)
            val topSeg = ckp.substringBefore('/')
            if (topSeg.isNotBlank() && topSeg !in candidates) candidates.add(topSeg)
        }
        for (fb in listOf("workspace", "", ".")) {
            if (fb !in candidates) candidates.add(fb)
        }

        var lastStatus = 404
        var lastText = ""

        for ((idx, cand) in candidates.withIndex()) {
            val baseUrl = if (cand.isNotBlank()) {
                val enc = cand.split("/").joinToString("/") { URLEncoder.encode(it, "UTF-8") }
                "$ENVIRONMENTS_BASE_URL/$cleanId/files/$enc"
            } else {
                "$ENVIRONMENTS_BASE_URL/$cleanId/files"
            }
            val urlBuilder = baseUrl.toHttpUrl().newBuilder()
            if (recursive) urlBuilder.addQueryParameter("recursive", "true")

            val req = Request.Builder()
                .url(urlBuilder.build())
                .get()
                .addHeader("Content-Type", "application/json")
                .addHeader("x-goog-api-key", apiKey.trim())
                .build()

            httpClient.newCall(req).execute().use { resp ->
                val bodyStr = resp.body?.string() ?: "{}"
                if (resp.code < 400) {
                    val data = JSONObject(bodyStr.ifBlank { "{}" })
                    val filesNorm = extractFilesListFromResponse(data)
                    if (filesNorm.isNotEmpty() || idx == 0) {
                        return@withContext (cand.ifBlank { "/" } to filesNorm)
                    }
                } else {
                    lastStatus = resp.code
                    lastText = bodyStr
                    if (idx == 0 && cleanPath !in listOf("workspace", "", "/")) {
                        return@use
                    }
                }
            }
            if (idx == 0 && cleanPath !in listOf("workspace", "", "/")) break
        }

        throw IllegalStateException("Erro ao listar arquivos do ambiente '$cleanId' (HTTP $lastStatus): $lastText")
    }

    private fun normalizeEnvTargetPath(rawPath: String?, cleanId: String): String {
        if (rawPath.isNullOrBlank()) return ""
        var p = rawPath.trim().replace("\\", "/")
        val marker = "environments/$cleanId/files/"
        if (p.contains(marker)) {
            p = p.substringAfter(marker)
        } else if (p.startsWith("environments/") && p.contains("/files/")) {
            p = p.substringAfter("/files/")
        }
        return p.trimStart('/')
    }

    private fun extractTargetFromTarBytes(
        tarBytes: ByteArray,
        targetPath: String
    ): DownloadedResource? {
        val cleanTarget = targetPath.trim('/')
        val baseName = cleanTarget.substringAfterLast('/').ifBlank { "arquivo" }
        val wantsRawTar = baseName.lowercase().let {
            it.endsWith(".tar") || it.endsWith(".tar.gz") || it.endsWith(".tgz")
        }
        if (wantsRawTar) return null

        val entries = mutableListOf<Pair<String, ByteArray>>()
        try {
            val rawStream = try {
                GzipCompressorInputStream(BufferedInputStream(ByteArrayInputStream(tarBytes)))
            } catch (_: Exception) {
                ByteArrayInputStream(tarBytes)
            }
            TarArchiveInputStream(rawStream).use { tarIn ->
                var entry = tarIn.nextTarEntry
                while (entry != null) {
                    if (!entry.isDirectory) {
                        val name = entry.name.removePrefix("./").trimStart('/')
                        val bytes = tarIn.readBytes()
                        entries.add(name to bytes)
                    }
                    entry = tarIn.nextTarEntry
                }
            }
        } catch (_: Exception) {
            return null
        }

        if (entries.isEmpty()) return null

        if (entries.size == 1 && baseName.contains(".")) {
            val single = entries.first()
            return DownloadedResource(single.second, baseName, guessMimeType(baseName))
        }

        val candidates = listOf(
            cleanTarget,
            cleanTarget.removePrefix("workspace/"),
            "workspace/$cleanTarget"
        )
        for ((name, data) in entries) {
            if (name in candidates || name.endsWith("/$cleanTarget") || name == baseName) {
                val fname = name.substringAfterLast('/').ifBlank { baseName }
                return DownloadedResource(data, fname, guessMimeType(fname))
            }
        }

        val dirPrefixes = candidates.filter { it.isNotBlank() }.map { "${it.trimEnd('/')}/" }
        val matchingDirFiles = entries.filter { (name, _) ->
            dirPrefixes.any { pref -> name.startsWith(pref) }
        }
        if (matchingDirFiles.isNotEmpty()) {
            val repacked = packFilesAsTarGz(matchingDirFiles)
            return DownloadedResource(repacked, "${baseName.ifBlank { "folder" }}.tar.gz", "application/gzip")
        }

        return null
    }

    private suspend fun buildTarGzFromEnvironmentListing(
        cleanId: String,
        basePath: String,
        entries: List<EnvFileEntry>,
        apiKey: String
    ): DownloadedResource = coroutineScope {
        val fileEntries = entries.filter { !it.isDirectory && it.path.isNotBlank() }
        if (fileEntries.isEmpty()) {
            throw IllegalStateException("Nenhum arquivo encontrado em '${basePath.ifBlank { "workspace" }}' para compactar no snapshot.")
        }

        val sem = Semaphore(6)
        val jobs = fileEntries.map { entry ->
            async(Dispatchers.IO) {
                var fPath = normalizeEnvTargetPath(entry.path, cleanId)
                if (fPath.isBlank() && basePath.isNotBlank()) {
                    fPath = "${basePath.trimStart('/')}/${entry.name}".trimStart('/')
                }
                if (fPath.isBlank()) return@async null
                val encPath = fPath.split("/").joinToString("/") { URLEncoder.encode(it, "UTF-8") }
                val fileUrl = "$ENVIRONMENTS_BASE_URL/$cleanId/files/$encPath?alt=media"

                sem.withPermit {
                    try {
                        val req = Request.Builder()
                            .url(fileUrl)
                            .get()
                            .addHeader("x-goog-api-key", apiKey.trim())
                            .build()
                        httpClient.newCall(req).execute().use { r ->
                            val bytes = r.body?.bytes()
                            if (r.code == 200 && bytes != null && bytes.isNotEmpty()) {
                                val extracted = extractTargetFromTarBytes(bytes, fPath)
                                return@withPermit fPath to (extracted?.bytes ?: bytes)
                            }
                        }
                    } catch (_: Exception) {
                    }
                    null
                }
            }
        }

        val downloaded = jobs.awaitAll().filterNotNull()
        if (downloaded.isEmpty()) {
            throw IllegalStateException("Não foi possível baixar os arquivos listados no ambiente '$cleanId'.")
        }

        val tarGzBytes = packFilesAsTarGz(downloaded)
        val slug = basePath.ifBlank { "workspace" }.trim('/').replace("/", "_").ifBlank { "snapshot" }
        DownloadedResource(
            bytes = tarGzBytes,
            fileName = "environment_snapshot_${cleanId}_$slug.tar.gz",
            mimeType = "application/gzip"
        )
    }

    suspend fun downloadEnvironmentSnapshot(
        environmentId: String,
        path: String? = null,
        knownPaths: List<String> = emptyList(),
        apiKey: String
    ): DownloadedResource = withContext(Dispatchers.IO) {
        val cleanId = cleanEnvironmentId(environmentId)
        if (cleanId.isBlank() || cleanId == "remote") {
            throw IllegalArgumentException("Informe um environment_id válido.")
        }
        val targetPath = normalizeEnvTargetPath(path, cleanId)

        // Caso 1: Download do Snapshot Completo (sem path específico)
        if (targetPath.isBlank()) {
            val snapshotUrl = "$FILES_BASE_URL/environment-$cleanId:download?alt=media"
            try {
                val req = Request.Builder()
                    .url(snapshotUrl)
                    .get()
                    .addHeader("x-goog-api-key", apiKey.trim())
                    .build()
                httpClient.newCall(req).execute().use { resp ->
                    val bytes = resp.body?.bytes()
                    val ctype = resp.header("content-type") ?: "application/x-tar"
                    if (resp.code == 200 && bytes != null && bytes.isNotEmpty() && !ctype.lowercase().contains("application/json")) {
                        return@withContext DownloadedResource(
                            bytes = bytes,
                            fileName = "environment_snapshot_$cleanId.tar.gz",
                            mimeType = ctype
                        )
                    }
                }
            } catch (_: Exception) {
            }

            // Fallback: lista recursivamente os diretórios do ambiente e monta o .tar.gz
            val (_, filesList) = listEnvironmentFiles(
                environmentId = cleanId,
                path = "workspace",
                recursive = true,
                knownPaths = knownPaths,
                apiKey = apiKey
            )
            return@withContext buildTarGzFromEnvironmentListing(
                cleanId = cleanId,
                basePath = "workspace",
                entries = filesList,
                apiKey = apiKey
            )
        }

        // Caso 2: Download de um Arquivo ou Diretório específico
        val encPath = targetPath.split("/").joinToString("/") { URLEncoder.encode(it, "UTF-8") }
        val baseName = targetPath.trimEnd('/').substringAfterLast('/').ifBlank { "arquivo" }
        val guessedMime = guessMimeType(baseName)

        val candidateUrls = listOf(
            "$ENVIRONMENTS_BASE_URL/$cleanId/files/$encPath?alt=media",
            "$ENVIRONMENTS_BASE_URL/$cleanId/files/$encPath:download?alt=media",
            "https://generativelanguage.googleapis.com/download/v1beta/environments/$cleanId/files/$encPath?alt=media"
        )

        for (url in candidateUrls) {
            try {
                val req = Request.Builder()
                    .url(url)
                    .get()
                    .addHeader("x-goog-api-key", apiKey.trim())
                    .build()
                httpClient.newCall(req).execute().use { resp ->
                    val bytes = resp.body?.bytes()
                    if (resp.code == 200 && bytes != null && bytes.isNotEmpty()) {
                        val extracted = extractTargetFromTarBytes(bytes, targetPath)
                        if (extracted != null) return@withContext extracted
                        return@withContext DownloadedResource(bytes, baseName, guessedMime)
                    }
                }
            } catch (_: Exception) {
            }
        }

        // Fallback se for diretório: lista recursivamente e empacota como .tar.gz
        val (_, dirEntries) = listEnvironmentFiles(
            environmentId = cleanId,
            path = targetPath,
            recursive = true,
            knownPaths = knownPaths,
            apiKey = apiKey
        )
        buildTarGzFromEnvironmentListing(
            cleanId = cleanId,
            basePath = targetPath,
            entries = dirEntries,
            apiKey = apiKey
        )
    }

    fun formatSessionAsMarkdown(session: SessionModel): String {
        val sb = StringBuilder()
        sb.appendLine("# ${session.title}")
        sb.appendLine()
        sb.appendLine("- **Session ID:** `${session.id}`")
        sb.appendLine("- **Modo:** `${session.mode}`")
        sb.appendLine("- **Agente:** `${session.agentName.ifBlank { "N/A" }}`")
        sb.appendLine("- **Modelo:** `${session.modelName}`")
        sb.appendLine("- **Environment ID:** `${session.environmentId.ifBlank { "remote (novo)" }}`")
        sb.appendLine("- **Último Interaction ID:** `${session.lastInteractionId.ifBlank { "N/A" }}`")

        if (session.uploadedFiles.isNotEmpty()) {
            sb.appendLine("- **Arquivos Enviados via Environments API:**")
            for (uf in session.uploadedFiles) {
                sb.appendLine("  - `${uf.targetPath}` (${uf.sizeBytes} bytes, extract=${uf.extract})")
            }
        }
        sb.appendLine()
        sb.appendLine("---")
        sb.appendLine()

        session.turns.forEachIndexed { idx, turn ->
            sb.appendLine("## Turno ${idx + 1}")
            sb.appendLine("- **Interaction ID:** `${turn.interactionId}`")
            if (turn.previousInteractionId.isNotBlank()) {
                sb.appendLine("- **Previous Interaction ID:** `${turn.previousInteractionId}`")
            }
            if (turn.environmentId.isNotBlank()) {
                sb.appendLine("- **Environment ID:** `${turn.environmentId}`")
            }
            sb.appendLine("- **Status:** `${turn.status}`")
            sb.appendLine()
            sb.appendLine("### 👤 Usuário")
            sb.appendLine()
            sb.appendLine(turn.userInputText.ifBlank { "(Sem texto de entrada)" })
            sb.appendLine()

            for (step in turn.steps) {
                if (step.type == "thought" && step.textContent.isNotBlank()) {
                    sb.appendLine("<details><summary>💭 Pensamento do Agente</summary>")
                    sb.appendLine()
                    sb.appendLine(step.textContent)
                    sb.appendLine()
                    sb.appendLine("</details>")
                    sb.appendLine()
                } else if (step.type in listOf("function_call", "code_execution_call", "google_search_call")) {
                    sb.appendLine("🔧 **Tool Call (`${step.name}`):**")
                    sb.appendLine("```json")
                    sb.appendLine(step.argumentsJson)
                    sb.appendLine("```")
                    sb.appendLine()
                }
            }

            sb.appendLine("### 🤖 Antigravity / Gemini")
            sb.appendLine()
            sb.appendLine(turn.outputText.ifBlank { "(Sem resposta final textual)" })
            sb.appendLine()
            sb.appendLine("---")
            sb.appendLine()
        }
        return sb.toString()
    }

    fun generatePythonSnippet(session: SessionModel, activePrevId: String): String {
        val envVal = cleanEnvironmentId(session.environmentId).ifBlank { "remote" }
        val prevLine = if (activePrevId.isNotBlank()) {
            "    previous_interaction_id=\"$activePrevId\",\n"
        } else {
            ""
        }
        return if (session.mode == "agent") {
            """from google import genai

client = genai.Client(api_key="SUA_GEMINI_API_KEY")

interaction = client.interactions.create(
    agent="${session.agentName.ifBlank { "antigravity-preview-09-2026" }}",
    input="Continue a tarefa no mesmo ambiente sandbox...",
$prevLine    environment="$envVal",
    agent_config={
        "type": "antigravity",
        "model": "${session.modelName.ifBlank { "gemini-3.8-flash" }}",
    },
    store=True,
)

print("Interaction ID:", interaction.id)
print("Environment ID:", getattr(interaction, "environment_id", "$envVal"))
print(interaction.output_text)
"""
        } else {
            """from google import genai

client = genai.Client(api_key="SUA_GEMINI_API_KEY")

interaction = client.interactions.create(
    model="${session.modelName.ifBlank { "gemini-3.8-flash" }}",
    input="Sua mensagem aqui...",
$prevLine    store=True,
)

print("Interaction ID:", interaction.id)
print(interaction.output_text)
"""
        }
    }

    fun generateCurlSnippet(session: SessionModel, activePrevId: String): String {
        val payload = buildCreatePayload(
            prompt = "Sua próxima instrução aqui...",
            mode = session.mode,
            agentName = session.agentName,
            modelName = session.modelName,
            previousInteractionId = activePrevId.takeIf { it.isNotBlank() },
            environmentId = session.environmentId,
            maxTotalTokens = session.maxTotalTokens,
            systemInstruction = session.systemInstruction,
            sources = session.sources
        )
        return """curl -X POST "$INTERACTIONS_BASE_URL" \
  -H "Content-Type: application/json" \
  -H "x-goog-api-key: ${'$'}GEMINI_API_KEY" \
  -d '${payload.toString(2)}'"""
    }

    fun guessMimeType(path: String): String {
        val ext = path.substringAfterLast('.', "").lowercase()
        if (ext.isBlank()) return "application/octet-stream"
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
            ?: when (ext) {
                "md" -> "text/markdown"
                "py", "txt", "log", "sh", "yaml", "yml" -> "text/plain"
                "json" -> "application/json"
                "tar", "tgz", "gz" -> "application/gzip"
                else -> "application/octet-stream"
            }
    }
}
