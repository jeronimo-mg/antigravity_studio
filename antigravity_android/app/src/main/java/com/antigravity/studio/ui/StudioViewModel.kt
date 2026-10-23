package com.antigravity.studio.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.core.content.FileProvider
import androidx.documentfile.provider.DocumentFile
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.antigravity.studio.data.local.StudioRepository
import com.antigravity.studio.data.model.EnvFileEntry
import com.antigravity.studio.data.model.SessionModel
import com.antigravity.studio.data.model.SourceItem
import com.antigravity.studio.data.model.UploadedFileRecord
import com.antigravity.studio.data.model.nowIso
import com.antigravity.studio.data.remote.InteractionsClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File

data class StudioUiState(
    val apiKey: String = "",
    val hasApiKey: Boolean = false,
    val maskedApiKey: String = "",
    val sessions: List<SessionModel> = emptyList(),
    val activeSession: SessionModel? = null,
    val customPrevInteractionId: String? = null,
    val backgroundMode: Boolean = true,
    val isSending: Boolean = false,
    val isImporting: Boolean = false,
    val isUploading: Boolean = false,
    val isListingFiles: Boolean = false,
    val isDownloadingSnapshot: Boolean = false,
    val envCurrentPath: String = "workspace",
    val envFiles: List<EnvFileEntry> = emptyList(),
    val statusMessage: String? = null,
    val errorMessage: String? = null
) {
    val effectivePreviousInteractionId: String
        get() = customPrevInteractionId ?: activeSession?.lastInteractionId.orEmpty()
}

class StudioViewModel(
    private val repository: StudioRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(StudioUiState())
    val uiState: StateFlow<StudioUiState> = _uiState.asStateFlow()

    private var pollingJob: Job? = null

    init {
        viewModelScope.launch {
            val savedKey = repository.getApiKey()
            updateKeyState(savedKey)
            refreshSessions(selectFirstIfNone = true)
        }
    }

    private fun updateKeyState(key: String) {
        val clean = key.trim()
        val masked = if (clean.length > 8) {
            "${clean.take(4)}...${clean.takeLast(4)}"
        } else if (clean.isNotEmpty()) {
            "configurada"
        } else {
            ""
        }
        _uiState.update {
            it.copy(
                apiKey = clean,
                hasApiKey = clean.isNotEmpty(),
                maskedApiKey = masked
            )
        }
    }

    fun saveApiKey(newKey: String) {
        viewModelScope.launch {
            repository.setApiKey(newKey)
            updateKeyState(newKey)
            showStatus("Chave de API salva no banco SQLite local!")
        }
    }

    fun clearMessages() {
        _uiState.update { it.copy(statusMessage = null, errorMessage = null) }
    }

    fun showStatus(msg: String) {
        _uiState.update { it.copy(statusMessage = msg, errorMessage = null) }
    }

    fun showError(err: String) {
        _uiState.update { it.copy(errorMessage = err, statusMessage = null) }
    }

    fun setBackgroundMode(enabled: Boolean) {
        _uiState.update { it.copy(backgroundMode = enabled) }
    }

    fun setCustomPrevInteractionId(interactionId: String?) {
        _uiState.update { it.copy(customPrevInteractionId = interactionId?.trim()) }
    }

    suspend fun refreshSessions(selectFirstIfNone: Boolean = false) {
        val list = repository.listSessions()
        val currentActiveId = _uiState.value.activeSession?.id
        val active = when {
            currentActiveId != null -> repository.getSession(currentActiveId) ?: list.firstOrNull()?.let { repository.getSession(it.id) }
            selectFirstIfNone && list.isNotEmpty() -> repository.getSession(list.first().id)
            selectFirstIfNone && list.isEmpty() -> repository.createSession()
            else -> null
        }
        val finalList = if (list.isEmpty() && active != null) repository.listSessions() else list
        _uiState.update {
            it.copy(
                sessions = finalList,
                activeSession = active
            )
        }
        checkAndStartPollingIfNeeded(active)
    }

    fun selectSession(sessionId: String) {
        viewModelScope.launch {
            val full = repository.getSession(sessionId) ?: return@launch
            _uiState.update {
                it.copy(
                    activeSession = full,
                    customPrevInteractionId = null,
                    envFiles = emptyList()
                )
            }
            checkAndStartPollingIfNeeded(full)
        }
    }

    fun createNewSession(
        title: String = "Nova Conversa Antigravity",
        mode: String = "agent",
        agentName: String = "antigravity-preview-09-2026",
        modelName: String = "gemini-3.8-flash",
        environmentId: String = ""
    ) {
        viewModelScope.launch {
            val created = repository.createSession(
                title = title,
                mode = mode,
                agentName = agentName,
                modelName = modelName,
                environmentId = environmentId
            )
            val all = repository.listSessions()
            _uiState.update {
                it.copy(
                    sessions = all,
                    activeSession = created,
                    customPrevInteractionId = null,
                    envFiles = emptyList()
                )
            }
        }
    }

    private suspend fun populateChainIntoSession(
        sessionId: String,
        targetInteractionId: String,
        apiKey: String
    ): SessionModel? {
        val cleanTarget = targetInteractionId.trim()
        if (cleanTarget.isBlank() || apiKey.isBlank()) return repository.getSession(sessionId)
        val existing = repository.getSession(sessionId) ?: return null

        val chain = InteractionsClient.recoverConversationChain(cleanTarget, apiKey)
        if (chain.isEmpty()) return existing

        val lastItem = chain.last()
        val detectedAgent = lastItem.optString("agent").ifBlank { existing.agentName }
        val detectedModel = lastItem.optString("model")
            .ifBlank { lastItem.optJSONObject("agent_config")?.optString("model").orEmpty() }
            .ifBlank { existing.modelName }
        val detectedMode = if (lastItem.has("agent")) "agent" else existing.mode
        val detectedEnv = InteractionsClient.cleanEnvironmentId(
            lastItem.optString("environment_id")
                .ifBlank {
                    val envField = lastItem.opt("environment")
                    if (envField is String) envField else ""
                }
                .ifBlank { existing.environmentId }
        )

        val (firstUserText, _) = InteractionsClient.extractTextsFromInteraction(chain.first())
        val firstKnownLocal = repository.findTurnByInteractionId(chain.first().optString("id"))?.userInputText.orEmpty()
        val effectiveFirstUser = firstUserText.ifBlank { firstKnownLocal }

        val defaultTitles = setOf("Nova Conversa Antigravity", "Nova Conversa", "Conversa Antigravity")
        val autoTitle = if (existing.title.isBlank() || existing.title in defaultTitles || existing.title.startsWith("Continuação (")) {
            when {
                effectiveFirstUser.length > 48 -> "${effectiveFirstUser.take(48)}..."
                effectiveFirstUser.isNotBlank() -> effectiveFirstUser
                else -> "Conversa (${cleanTarget.take(12)}...)"
            }
        } else {
            existing.title
        }

        chain.forEachIndexed { idx, item ->
            val itemId = item.optString("id", "turn_${idx + 1}")
            val (uText, outText) = InteractionsClient.extractTextsFromInteraction(item)
            val knownLocalUser = repository.findTurnByInteractionId(itemId)?.userInputText.orEmpty()
            val itemEnv = InteractionsClient.cleanEnvironmentId(
                item.optString("environment_id").ifBlank { detectedEnv }
            )
            repository.upsertTurn(
                sessionId = sessionId,
                interactionId = itemId,
                turnIndex = idx + 1,
                previousInteractionId = item.optString("previous_interaction_id", ""),
                environmentId = itemEnv,
                userInputText = uText.ifBlank { knownLocalUser },
                outputText = outText,
                status = item.optString("status", "completed"),
                stepsJson = item.optJSONArray("steps")?.toString() ?: "[]",
                usageJson = item.optJSONObject("usage")?.toString() ?: "{}",
                rawJson = item.toString(),
                createdAt = item.optString("created").takeIf { it.isNotBlank() }
            )
        }

        return repository.updateSession(
            sessionId = sessionId,
            title = autoTitle,
            mode = detectedMode,
            agentName = detectedAgent,
            modelName = detectedModel,
            environmentId = detectedEnv,
            lastInteractionId = lastItem.optString("id", cleanTarget)
        )
    }

    fun loadPreviousConversationIntoActiveSession(overrideId: String? = null) {
        val current = _uiState.value.activeSession ?: return
        val targetId = (overrideId ?: _uiState.value.effectivePreviousInteractionId).trim()
        if (targetId.isBlank()) {
            showError("Informe um previous_interaction_id válido (ex: v1_...) para carregar a conversa.")
            return
        }
        val apiKey = _uiState.value.apiKey
        if (apiKey.isBlank()) {
            showError("Configure sua Gemini API Key para buscar a conversa na API.")
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isImporting = true, errorMessage = null) }
            try {
                val loaded = populateChainIntoSession(current.id, targetId, apiKey)
                val all = repository.listSessions()
                _uiState.update {
                    it.copy(
                        isImporting = false,
                        sessions = all,
                        activeSession = loaded,
                        customPrevInteractionId = null,
                        statusMessage = "${loaded?.turns?.size ?: 0} turno(s) carregado(s) na tela!"
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isImporting = false,
                        errorMessage = e.message ?: "Erro ao carregar conversa anterior."
                    )
                }
            }
        }
    }

    fun updateActiveSessionSettings(
        title: String? = null,
        mode: String? = null,
        agentName: String? = null,
        modelName: String? = null,
        environmentId: String? = null,
        lastInteractionId: String? = null,
        maxTotalTokens: Int? = null,
        systemInstruction: String? = null,
        sources: List<SourceItem>? = null
    ) {
        val current = _uiState.value.activeSession ?: return
        viewModelScope.launch {
            var updated = repository.updateSession(
                sessionId = current.id,
                title = title,
                mode = mode,
                agentName = agentName,
                modelName = modelName,
                environmentId = environmentId,
                lastInteractionId = lastInteractionId,
                maxTotalTokens = maxTotalTokens,
                systemInstruction = systemInstruction,
                sources = sources
            )
            val newLastId = lastInteractionId?.trim().orEmpty()
            val existingIds = current.turns.map { it.interactionId }.toSet()
            if (newLastId.isNotBlank() && newLastId !in existingIds && _uiState.value.apiKey.isNotBlank()) {
                _uiState.update { it.copy(isImporting = true) }
                try {
                    val loaded = populateChainIntoSession(current.id, newLastId, _uiState.value.apiKey)
                    if (loaded != null) {
                        updated = loaded
                    }
                } catch (_: Exception) {
                } finally {
                    _uiState.update { it.copy(isImporting = false) }
                }
            }
            val all = repository.listSessions()
            _uiState.update {
                it.copy(
                    sessions = all,
                    activeSession = updated
                )
            }
            showStatus(
                if (newLastId.isNotBlank() && newLastId !in existingIds && (updated?.turns?.isNotEmpty() == true)) {
                    "Conversa anterior (${updated?.turns?.size} turno(s)) carregada na tela!"
                } else {
                    "Configurações da sessão atualizadas!"
                }
            )
        }
    }

    fun deleteSession(sessionId: String) {
        viewModelScope.launch {
            repository.deleteSession(sessionId)
            val remaining = repository.listSessions()
            val nextActive = if (remaining.isNotEmpty()) {
                repository.getSession(remaining.first().id)
            } else {
                repository.createSession()
            }
            val finalSessions = repository.listSessions()
            _uiState.update {
                it.copy(
                    sessions = finalSessions,
                    activeSession = nextActive,
                    customPrevInteractionId = null
                )
            }
        }
    }

    fun sendMessage(promptText: String) {
        val prompt = promptText.trim()
        var session = _uiState.value.activeSession ?: return
        val apiKey = _uiState.value.apiKey
        if (prompt.isEmpty()) return
        if (apiKey.isBlank()) {
            showError("Configure sua Gemini API Key clicando no ícone de chave 🔑 no topo.")
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isSending = true, errorMessage = null) }
            try {
                val prevId = _uiState.value.effectivePreviousInteractionId
                if (prevId.isNotBlank() && session.turns.isEmpty()) {
                    try {
                        val loaded = populateChainIntoSession(session.id, prevId, apiKey)
                        if (loaded != null) {
                            session = loaded
                        }
                    } catch (_: Exception) {
                    }
                }
                val envId = session.environmentId
                val uploadedFiles = session.uploadedFiles.ifEmpty {
                    repository.findUploadedFilesForEnvironment(envId)
                }

                // Monta resumo dos últimos 6 turnos para fallback resiliente se previous_interaction_id falhar
                val historyLines = session.turns.takeLast(6)
                    .filter { it.status in listOf("completed", "incomplete") }
                    .mapNotNull { t ->
                        if (t.userInputText.isNotBlank() || t.outputText.isNotBlank()) {
                            "Usuário: ${t.userInputText.take(600)}\nAgente: ${t.outputText.take(1200)}"
                        } else null
                    }
                val fallbackHistory = historyLines.takeIf { it.isNotEmpty() }?.joinToString("\n---\n")

                // Se algum turno anterior apontando para prevId (ou o último turno da sessão)
                // falhou ou foi cancelado, inicia uma nova cadeia limpa mantendo environmentId e histórico
                var effectivePrevForRequest = prevId.trim()
                var effectivePrompt = prompt
                var proactivelyDroppedPrev = false
                if (effectivePrevForRequest.isNotEmpty() && session.turns.isNotEmpty()) {
                    val chainPoisoned = session.turns.any { t ->
                        t.status in listOf("failed", "cancelled") &&
                            (t.previousInteractionId.trim() == effectivePrevForRequest ||
                                t.interactionId.trim() == effectivePrevForRequest)
                    } || (session.turns.last().status in listOf("failed", "cancelled"))
                    if (chainPoisoned) {
                        effectivePrevForRequest = ""
                        proactivelyDroppedPrev = true
                        if (!fallbackHistory.isNullOrBlank()) {
                            effectivePrompt = "[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:\n$fallbackHistory\n\n[NOVA INSTRUÇÃO DO USUÁRIO]:\n$prompt"
                        }
                    }
                }

                val payload = InteractionsClient.buildCreatePayload(
                    prompt = effectivePrompt,
                    mode = session.mode,
                    agentName = session.agentName,
                    modelName = session.modelName,
                    previousInteractionId = effectivePrevForRequest.takeIf { it.isNotBlank() },
                    environmentId = envId,
                    maxTotalTokens = session.maxTotalTokens,
                    systemInstruction = session.systemInstruction,
                    background = _uiState.value.backgroundMode,
                    sources = session.sources,
                    uploadedFiles = uploadedFiles
                )

                val resp = InteractionsClient.createInteraction(
                    payload = payload,
                    apiKey = apiKey,
                    fallbackHistoryText = fallbackHistory
                )
                if (proactivelyDroppedPrev) {
                    resp.put("_dropped_previous_interaction_id", true)
                }

                val newInteractionId = resp.optString("id", "turn_${System.currentTimeMillis()}")
                val returnedEnvId = InteractionsClient.cleanEnvironmentId(
                    resp.optString("environment_id").ifBlank { envId }
                )
                val effectivePrevId = if (resp.has("_dropped_previous_interaction_id")) "" else effectivePrevForRequest
                val (_, outText) = InteractionsClient.extractTextsFromInteraction(resp, fallbackUserInput = prompt)
                val turnStatus = resp.optString("status", "completed")
                val nextIndex = session.turns.size + 1

                var newTitle = session.title
                if (nextIndex == 1 && newTitle in listOf("Nova Conversa Antigravity", "Nova Conversa")) {
                    newTitle = if (prompt.length > 48) "${prompt.take(48)}..." else prompt
                }

                repository.upsertTurn(
                    sessionId = session.id,
                    interactionId = newInteractionId,
                    turnIndex = nextIndex,
                    previousInteractionId = effectivePrevId,
                    environmentId = returnedEnvId,
                    userInputText = prompt,
                    outputText = outText,
                    status = turnStatus,
                    stepsJson = resp.optJSONArray("steps")?.toString() ?: "[]",
                    usageJson = resp.optJSONObject("usage")?.toString() ?: "{}",
                    rawJson = resp.toString(),
                    createdAt = resp.optString("created").takeIf { it.isNotBlank() }
                )

                val nextLastId = if (turnStatus !in listOf("failed", "cancelled")) newInteractionId else ""
                val updatedSession = repository.updateSession(
                    sessionId = session.id,
                    title = newTitle,
                    environmentId = returnedEnvId,
                    lastInteractionId = nextLastId
                )
                val allSessions = repository.listSessions()

                _uiState.update {
                    it.copy(
                        isSending = false,
                        sessions = allSessions,
                        activeSession = updatedSession,
                        customPrevInteractionId = null
                    )
                }

                checkAndStartPollingIfNeeded(updatedSession)
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isSending = false,
                        errorMessage = e.message ?: "Erro ao enviar mensagem."
                    )
                }
            }
        }
    }

    private fun checkAndStartPollingIfNeeded(session: SessionModel?) {
        pollingJob?.cancel()
        if (session == null) return
        val inProgressTurn = session.turns.lastOrNull { it.status == "in_progress" } ?: return

        pollingJob = viewModelScope.launch {
            while (isActive) {
                delay(3200L)
                val curSession = _uiState.value.activeSession ?: break
                val targetTurn = curSession.turns.lastOrNull { it.status == "in_progress" } ?: break
                try {
                    pollSingleTurnInternal(curSession, targetTurn.interactionId)
                } catch (_: Exception) {
                }
            }
        }
    }

    fun pollTurnManually(interactionId: String) {
        val curSession = _uiState.value.activeSession ?: return
        viewModelScope.launch {
            try {
                pollSingleTurnInternal(curSession, interactionId)
            } catch (e: Exception) {
                showError(e.message ?: "Erro ao atualizar turno.")
            }
        }
    }

    private suspend fun pollSingleTurnInternal(session: SessionModel, interactionId: String) {
        val apiKey = _uiState.value.apiKey
        if (apiKey.isBlank()) return

        val resp = InteractionsClient.getInteraction(
            interactionId = interactionId,
            includeInput = true,
            apiKey = apiKey
        )
        val existingTurn = session.turns.find { it.interactionId == interactionId }
        val turnIndex = existingTurn?.turnIndex ?: (session.turns.size + 1)
        val fallbackInput = existingTurn?.userInputText.orEmpty()

        val (rawUText, outText) = InteractionsClient.extractTextsFromInteraction(resp, fallbackInput)
        val uText = if (fallbackInput.isNotBlank() && rawUText.startsWith("[HISTÓRICO RESUMIDO DA SESSÃO ANTERIOR]:")) {
            fallbackInput
        } else {
            rawUText
        }
        val returnedEnvId = InteractionsClient.cleanEnvironmentId(
            resp.optString("environment_id").ifBlank { session.environmentId }
        )
        val turnStatus = resp.optString("status", "completed")

        repository.upsertTurn(
            sessionId = session.id,
            interactionId = interactionId,
            turnIndex = turnIndex,
            previousInteractionId = resp.optString("previous_interaction_id")
                .ifBlank { existingTurn?.previousInteractionId.orEmpty() },
            environmentId = returnedEnvId,
            userInputText = uText,
            outputText = outText,
            status = turnStatus,
            stepsJson = resp.optJSONArray("steps")?.toString() ?: existingTurn?.stepsJson ?: "[]",
            usageJson = resp.optJSONObject("usage")?.toString() ?: existingTurn?.usageJson ?: "{}",
            rawJson = resp.toString(),
            createdAt = resp.optString("created").takeIf { it.isNotBlank() } ?: existingTurn?.createdAt
        )

        val nextLastId = if (turnStatus in listOf("failed", "cancelled")) {
            ""
        } else {
            interactionId
        }

        val updated = repository.updateSession(
            sessionId = session.id,
            environmentId = returnedEnvId,
            lastInteractionId = nextLastId
        )
        val all = repository.listSessions()
        _uiState.update {
            it.copy(
                sessions = all,
                activeSession = updated
            )
        }
    }

    fun cancelTurn(interactionId: String) {
        val session = _uiState.value.activeSession ?: return
        val apiKey = _uiState.value.apiKey
        viewModelScope.launch {
            try {
                pollingJob?.cancel()
                val resp = InteractionsClient.cancelInteraction(interactionId, apiKey)
                val existingTurn = session.turns.find { it.interactionId == interactionId }
                val turnIndex = existingTurn?.turnIndex ?: (session.turns.size + 1)
                val (uText, outText) = InteractionsClient.extractTextsFromInteraction(
                    resp,
                    existingTurn?.userInputText.orEmpty()
                )
                val returnedEnvId = InteractionsClient.cleanEnvironmentId(
                    resp.optString("environment_id")
                        .ifBlank { existingTurn?.environmentId.orEmpty() }
                        .ifBlank { session.environmentId }
                )

                repository.upsertTurn(
                    sessionId = session.id,
                    interactionId = interactionId,
                    turnIndex = turnIndex,
                    previousInteractionId = existingTurn?.previousInteractionId.orEmpty(),
                    environmentId = returnedEnvId,
                    userInputText = uText,
                    outputText = outText.ifBlank { existingTurn?.outputText.orEmpty() }
                        .ifBlank { "(Execução cancelada pelo usuário)" },
                    status = "cancelled",
                    stepsJson = resp.optJSONArray("steps")?.toString() ?: existingTurn?.stepsJson ?: "[]",
                    usageJson = resp.optJSONObject("usage")?.toString() ?: existingTurn?.usageJson ?: "{}",
                    rawJson = resp.toString(),
                    createdAt = existingTurn?.createdAt
                )

                val updated = repository.updateSession(
                    sessionId = session.id,
                    environmentId = returnedEnvId,
                    lastInteractionId = ""
                )
                _uiState.update {
                    it.copy(
                        sessions = repository.listSessions(),
                        activeSession = updated,
                        statusMessage = "Interação cancelada."
                    )
                }
            } catch (e: Exception) {
                showError(e.message ?: "Erro ao cancelar interação.")
            }
        }
    }

    fun forkFromTurn(interactionId: String, customTitle: String? = null) {
        val session = _uiState.value.activeSession ?: return
        viewModelScope.launch {
            val forked = repository.forkSession(session.id, interactionId, customTitle) ?: return@launch
            val all = repository.listSessions()
            _uiState.update {
                it.copy(
                    sessions = all,
                    activeSession = forked,
                    customPrevInteractionId = null,
                    statusMessage = "Conversa ramificada (Fork) com sucesso!"
                )
            }
        }
    }

    fun importChainById(
        interactionId: String,
        environmentId: String = "",
        title: String = "",
        mode: String = "agent",
        agentName: String = "antigravity-preview-09-2026",
        modelName: String = "gemini-3.8-flash",
        fetchHistory: Boolean = true
    ) {
        val targetId = interactionId.trim()
        if (targetId.isBlank()) {
            showError("Informe o interaction_id (ex: v1_...).")
            return
        }
        val apiKey = _uiState.value.apiKey

        viewModelScope.launch {
            _uiState.update { it.copy(isImporting = true, errorMessage = null) }
            try {
                if (!fetchHistory) {
                    val effTitle = title.ifBlank { "Continuação (${targetId.take(14)}...)" }
                    val created = repository.createSession(
                        title = effTitle,
                        mode = mode,
                        agentName = agentName,
                        modelName = modelName,
                        environmentId = InteractionsClient.cleanEnvironmentId(environmentId),
                        lastInteractionId = targetId
                    )
                    _uiState.update {
                        it.copy(
                            isImporting = false,
                            sessions = repository.listSessions(),
                            activeSession = created,
                            statusMessage = "Sessão vinculada ao ID $targetId!"
                        )
                    }
                    return@launch
                }

                val chain = InteractionsClient.recoverConversationChain(targetId, apiKey)
                if (chain.isEmpty()) {
                    throw IllegalStateException("Nenhum turno encontrado para o ID informado.")
                }

                val lastItem = chain.last()
                val detectedAgent = lastItem.optString("agent").ifBlank { agentName }
                val detectedModel = lastItem.optString("model")
                    .ifBlank { lastItem.optJSONObject("agent_config")?.optString("model").orEmpty() }
                    .ifBlank { modelName }
                val detectedMode = if (lastItem.has("agent")) "agent" else mode
                val detectedEnv = InteractionsClient.cleanEnvironmentId(
                    lastItem.optString("environment_id")
                        .ifBlank {
                            val envField = lastItem.opt("environment")
                            if (envField is String) envField else ""
                        }
                        .ifBlank { environmentId }
                )

                val (firstUserText, _) = InteractionsClient.extractTextsFromInteraction(chain.first())
                val autoTitle = title.ifBlank {
                    when {
                        firstUserText.length > 48 -> "${firstUserText.take(48)}..."
                        firstUserText.isNotBlank() -> firstUserText
                        else -> "Importada (${targetId.take(12)}...)"
                    }
                }

                val created = repository.createSession(
                    title = autoTitle,
                    mode = detectedMode,
                    agentName = detectedAgent,
                    modelName = detectedModel,
                    environmentId = detectedEnv,
                    lastInteractionId = lastItem.optString("id", targetId)
                )

                chain.forEachIndexed { idx, item ->
                    val (uText, outText) = InteractionsClient.extractTextsFromInteraction(item)
                    val itemEnv = InteractionsClient.cleanEnvironmentId(
                        item.optString("environment_id").ifBlank { detectedEnv }
                    )
                    repository.upsertTurn(
                        sessionId = created.id,
                        interactionId = item.optString("id", "turn_${idx + 1}"),
                        turnIndex = idx + 1,
                        previousInteractionId = item.optString("previous_interaction_id", ""),
                        environmentId = itemEnv,
                        userInputText = uText,
                        outputText = outText,
                        status = item.optString("status", "completed"),
                        stepsJson = item.optJSONArray("steps")?.toString() ?: "[]",
                        usageJson = item.optJSONObject("usage")?.toString() ?: "{}",
                        rawJson = item.toString(),
                        createdAt = item.optString("created").takeIf { it.isNotBlank() }
                    )
                }

                val fullSession = repository.getSession(created.id)
                _uiState.update {
                    it.copy(
                        isImporting = false,
                        sessions = repository.listSessions(),
                        activeSession = fullSession,
                        statusMessage = "${chain.size} turno(s) recuperado(s) com sucesso!"
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isImporting = false,
                        errorMessage = e.message ?: "Erro ao importar conversa."
                    )
                }
            }
        }
    }

    fun provisionEnvironmentForActiveSession(fromEnvironment: String? = null) {
        val session = _uiState.value.activeSession ?: return
        val apiKey = _uiState.value.apiKey
        viewModelScope.launch {
            _uiState.update { it.copy(isUploading = true, errorMessage = null) }
            try {
                val envData = InteractionsClient.createRemoteEnvironment(
                    sources = session.sources,
                    fromEnvironment = fromEnvironment,
                    apiKey = apiKey
                )
                val newEnvId = envData.optString("id")
                val updated = repository.updateSession(session.id, environmentId = newEnvId)
                _uiState.update {
                    it.copy(
                        isUploading = false,
                        sessions = repository.listSessions(),
                        activeSession = updated,
                        statusMessage = "Ambiente provisionado: $newEnvId"
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isUploading = false,
                        errorMessage = e.message ?: "Erro ao provisionar ambiente."
                    )
                }
            }
        }
    }

    fun uploadUrisToEnvironment(
        context: Context,
        uris: List<Uri>,
        targetPath: String = "/workspace",
        overwrite: Boolean = true,
        extract: Boolean = false,
        packAsTarGz: Boolean = false,
        resumable: Boolean = false
    ) {
        val session = _uiState.value.activeSession ?: return
        val apiKey = _uiState.value.apiKey
        if (uris.isEmpty()) return

        viewModelScope.launch {
            _uiState.update { it.copy(isUploading = true, errorMessage = null) }
            try {
                var envId = InteractionsClient.cleanEnvironmentId(session.environmentId)
                if (envId.isBlank() || envId == "remote") {
                    val envResp = InteractionsClient.createRemoteEnvironment(
                        sources = session.sources,
                        apiKey = apiKey
                    )
                    envId = InteractionsClient.cleanEnvironmentId(envResp.optString("id"))
                    repository.updateSession(session.id, environmentId = envId)
                }

                val filesPairs = mutableListOf<Pair<String, ByteArray>>()
                for (uri in uris) {
                    val name = resolveFileName(context, uri)
                    val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                        ?: continue
                    filesPairs.add(name to bytes)
                }
                if (filesPairs.isEmpty()) {
                    throw IllegalStateException("Não foi possível ler os arquivos selecionados.")
                }

                val shouldPack = packAsTarGz || filesPairs.size > 1
                val uploadRes = if (shouldPack) {
                    val tarGzBytes = InteractionsClient.packFilesAsTarGz(filesPairs)
                    InteractionsClient.uploadFileToEnvironment(
                        environmentId = envId,
                        targetPath = targetPath,
                        fileBytes = tarGzBytes,
                        mimeType = "application/x-tar",
                        overwrite = overwrite,
                        extract = true,
                        resumable = resumable,
                        apiKey = apiKey
                    )
                } else {
                    val (singleName, singleBytes) = filesPairs.first()
                    val effectiveTarget = if (targetPath.trimEnd('/') in listOf("/workspace", "workspace")) {
                        "/workspace/$singleName"
                    } else {
                        targetPath
                    }
                    InteractionsClient.uploadFileToEnvironment(
                        environmentId = envId,
                        targetPath = effectiveTarget,
                        fileBytes = singleBytes,
                        overwrite = overwrite,
                        extract = extract,
                        resumable = resumable,
                        apiKey = apiKey
                    )
                }

                val summaryLabel = if (shouldPack) {
                    "Pacote (${filesPairs.size} arquivo(s))"
                } else {
                    filesPairs.first().first
                }

                val record = UploadedFileRecord(
                    label = summaryLabel,
                    summary = summaryLabel,
                    targetPath = uploadRes.optString("target_path", targetPath),
                    sizeBytes = uploadRes.optLong("size_bytes", 0L),
                    mimeType = uploadRes.optString("mime_type", "application/octet-stream"),
                    extract = uploadRes.optBoolean("extract", shouldPack || extract),
                    overwrite = overwrite,
                    resumable = resumable,
                    uploadMode = uploadRes.optString("upload_mode", "direct_put"),
                    fileCount = filesPairs.size,
                    environmentId = envId,
                    uploadedAt = nowIso()
                )

                val currentUploads = session.uploadedFiles.toMutableList().apply { add(record) }
                val updated = repository.updateSession(
                    sessionId = session.id,
                    environmentId = envId,
                    uploadedFiles = currentUploads
                )

                _uiState.update {
                    it.copy(
                        isUploading = false,
                        sessions = repository.listSessions(),
                        activeSession = updated,
                        statusMessage = "Upload concluído em ${record.targetPath} ($envId)!"
                    )
                }
                loadEnvironmentFiles(path = "workspace")
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isUploading = false,
                        errorMessage = e.message ?: "Erro ao enviar arquivos para o sandbox."
                    )
                }
            }
        }
    }

    fun uploadFolderTreeToEnvironment(
        context: Context,
        treeUri: Uri,
        targetPath: String = "/workspace",
        overwrite: Boolean = true,
        resumable: Boolean = false
    ) {
        val session = _uiState.value.activeSession ?: return
        val apiKey = _uiState.value.apiKey

        viewModelScope.launch {
            _uiState.update { it.copy(isUploading = true, errorMessage = null) }
            try {
                val rootDoc = DocumentFile.fromTreeUri(context, treeUri)
                    ?: throw IllegalStateException("Não foi possível acessar a pasta selecionada.")

                val filesPairs = mutableListOf<Pair<String, ByteArray>>()
                collectDocumentTreeFiles(context, rootDoc, "", filesPairs)
                if (filesPairs.isEmpty()) {
                    throw IllegalStateException("A pasta selecionada está vazia.")
                }

                var envId = InteractionsClient.cleanEnvironmentId(session.environmentId)
                if (envId.isBlank() || envId == "remote") {
                    val envResp = InteractionsClient.createRemoteEnvironment(
                        sources = session.sources,
                        apiKey = apiKey
                    )
                    envId = InteractionsClient.cleanEnvironmentId(envResp.optString("id"))
                    repository.updateSession(session.id, environmentId = envId)
                }

                val tarGzBytes = InteractionsClient.packFilesAsTarGz(filesPairs)
                val uploadRes = InteractionsClient.uploadFileToEnvironment(
                    environmentId = envId,
                    targetPath = targetPath,
                    fileBytes = tarGzBytes,
                    mimeType = "application/x-tar",
                    overwrite = overwrite,
                    extract = true,
                    resumable = resumable,
                    apiKey = apiKey
                )

                val folderName = rootDoc.name ?: "pasta"
                val label = "Pasta '$folderName' (${filesPairs.size} arquivos)"
                val record = UploadedFileRecord(
                    label = label,
                    summary = label,
                    targetPath = uploadRes.optString("target_path", targetPath),
                    sizeBytes = tarGzBytes.size.toLong(),
                    mimeType = "application/x-tar",
                    extract = true,
                    overwrite = overwrite,
                    resumable = resumable,
                    uploadMode = uploadRes.optString("upload_mode", "direct_put"),
                    fileCount = filesPairs.size,
                    environmentId = envId,
                    uploadedAt = nowIso()
                )

                val currentUploads = session.uploadedFiles.toMutableList().apply { add(record) }
                val updated = repository.updateSession(
                    sessionId = session.id,
                    environmentId = envId,
                    uploadedFiles = currentUploads
                )

                _uiState.update {
                    it.copy(
                        isUploading = false,
                        sessions = repository.listSessions(),
                        activeSession = updated,
                        statusMessage = "Pasta '$folderName' extraída em ${record.targetPath}!"
                    )
                }
                loadEnvironmentFiles(path = targetPath.trimStart('/').ifBlank { "workspace" })
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isUploading = false,
                        errorMessage = e.message ?: "Erro ao enviar pasta para o ambiente."
                    )
                }
            }
        }
    }

    private fun collectDocumentTreeFiles(
        context: Context,
        dir: DocumentFile,
        prefix: String,
        out: MutableList<Pair<String, ByteArray>>
    ) {
        for (child in dir.listFiles()) {
            val childName = child.name ?: continue
            val relPath = if (prefix.isEmpty()) childName else "$prefix/$childName"
            if (child.isDirectory) {
                collectDocumentTreeFiles(context, child, relPath, out)
            } else if (child.isFile) {
                val bytes = context.contentResolver.openInputStream(child.uri)?.use { it.readBytes() }
                if (bytes != null) {
                    out.add(relPath to bytes)
                }
            }
        }
    }

    fun loadEnvironmentFiles(path: String = "workspace") {
        val session = _uiState.value.activeSession ?: return
        val envId = InteractionsClient.cleanEnvironmentId(session.environmentId)
        val apiKey = _uiState.value.apiKey
        if (envId.isBlank() || envId == "remote") {
            showError("Esta sessão ainda não possui um environment_id ativo.")
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isListingFiles = true, errorMessage = null) }
            try {
                val knownPaths = session.uploadedFiles.map { it.targetPath } +
                    session.sources.map { it.target }
                val (resolvedPath, entries) = InteractionsClient.listEnvironmentFiles(
                    environmentId = envId,
                    path = path,
                    recursive = true,
                    knownPaths = knownPaths,
                    apiKey = apiKey
                )
                _uiState.update {
                    it.copy(
                        isListingFiles = false,
                        envCurrentPath = resolvedPath,
                        envFiles = entries
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isListingFiles = false,
                        errorMessage = e.message ?: "Erro ao listar arquivos do sandbox."
                    )
                }
            }
        }
    }

    fun downloadAndShareEnvironmentFileOrSnapshot(
        context: Context,
        path: String? = null
    ) {
        val session = _uiState.value.activeSession ?: return
        val envId = InteractionsClient.cleanEnvironmentId(session.environmentId)
        val apiKey = _uiState.value.apiKey
        if (envId.isBlank() || envId == "remote") {
            showError("Nenhum environment_id ativo para download.")
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isDownloadingSnapshot = true, errorMessage = null) }
            try {
                val knownPaths = session.uploadedFiles.map { it.targetPath } +
                    session.sources.map { it.target }
                val resource = InteractionsClient.downloadEnvironmentSnapshot(
                    environmentId = envId,
                    path = path,
                    knownPaths = knownPaths,
                    apiKey = apiKey
                )

                val exportDir = File(context.cacheDir, "exports").apply { mkdirs() }
                val outFile = File(exportDir, resource.fileName)
                outFile.writeBytes(resource.bytes)

                val uri = FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    outFile
                )

                val shareIntent = Intent(Intent.ACTION_SEND).apply {
                    type = resource.mimeType
                    putExtra(Intent.EXTRA_STREAM, uri)
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                context.startActivity(
                    Intent.createChooser(shareIntent, "Salvar / Compartilhar ${resource.fileName}")
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )

                _uiState.update {
                    it.copy(
                        isDownloadingSnapshot = false,
                        statusMessage = "Download concluído: ${resource.fileName}"
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isDownloadingSnapshot = false,
                        errorMessage = e.message ?: "Erro no download do sandbox."
                    )
                }
            }
        }
    }

    fun exportAndShareSessionMarkdown(context: Context) {
        val session = _uiState.value.activeSession ?: return
        val md = InteractionsClient.formatSessionAsMarkdown(session)
        val exportDir = File(context.cacheDir, "exports").apply { mkdirs() }
        val outFile = File(exportDir, "${session.id}.md")
        outFile.writeText(md, Charsets.UTF_8)

        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            outFile
        )
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "text/markdown"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_TEXT, md)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(
            Intent.createChooser(intent, "Exportar Conversa (.md)")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    private fun resolveFileName(context: Context, uri: Uri): String {
        var result: String? = null
        if (uri.scheme == "content") {
            context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (idx >= 0) result = cursor.getString(idx)
                }
            }
        }
        if (result.isNullOrBlank()) {
            result = uri.path?.substringAfterLast('/')
        }
        return result?.ifBlank { "arquivo" } ?: "arquivo"
    }

    class Factory(private val repository: StudioRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return StudioViewModel(repository) as T
        }
    }
}
