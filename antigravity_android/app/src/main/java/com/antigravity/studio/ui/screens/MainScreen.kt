package com.antigravity.studio.ui.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.CallSplit
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.antigravity.studio.data.model.TurnModel
import com.antigravity.studio.ui.StudioViewModel
import com.antigravity.studio.ui.components.ApiKeyDialog
import com.antigravity.studio.ui.components.CodeAndExportDialog
import com.antigravity.studio.ui.components.ImportByIdDialog
import com.antigravity.studio.ui.components.RunSettingsSheet
import com.antigravity.studio.ui.components.SandboxManagerSheet
import com.antigravity.studio.ui.theme.Amber400
import com.antigravity.studio.ui.theme.Emerald400
import com.antigravity.studio.ui.theme.Indigo500
import com.antigravity.studio.ui.theme.Rose400
import com.antigravity.studio.ui.theme.Sky400
import com.antigravity.studio.ui.theme.Slate400
import com.antigravity.studio.ui.theme.Slate800
import com.antigravity.studio.ui.theme.Slate900
import com.antigravity.studio.ui.theme.Slate950
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: StudioViewModel) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val listState = rememberLazyListState()

    var promptInput by remember { mutableStateOf("") }
    var showApiKeyDialog by remember { mutableStateOf(false) }
    var showImportDialog by remember { mutableStateOf(false) }
    var showRunSettings by remember { mutableStateOf(false) }
    var showSandboxSheet by remember { mutableStateOf(false) }
    var showCodeDialog by remember { mutableStateOf(false) }

    val activeSession = uiState.activeSession

    // Rola automaticamente para o último turno ao receber nova resposta
    LaunchedEffect(activeSession?.turns?.size) {
        val count = activeSession?.turns?.size ?: 0
        if (count > 0) {
            listState.animateScrollToItem(count - 1)
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet(
                drawerContainerColor = Slate900,
                modifier = Modifier.width(310.dp)
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxHeight()
                        .padding(14.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text(
                        "🚀 Antigravity Studio",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        color = Color.White
                    )
                    Text(
                        "Android Native Playground",
                        style = MaterialTheme.typography.labelSmall,
                        color = Sky400
                    )

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = {
                                viewModel.createNewSession()
                                scope.launch { drawerState.close() }
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.Add, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Nova", fontSize = 12.sp)
                        }
                        OutlinedButton(
                            onClick = {
                                showImportDialog = true
                                scope.launch { drawerState.close() }
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.Link, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Importar ID", fontSize = 12.sp)
                        }
                    }

                    HorizontalDivider(color = Slate800)

                    Text(
                        "SESSÕES SALVAS (${uiState.sessions.size})",
                        style = MaterialTheme.typography.labelSmall,
                        color = Slate400
                    )

                    LazyColumn(
                        modifier = Modifier.weight(1f),
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        items(uiState.sessions, key = { it.id }) { sess ->
                            val isSelected = sess.id == activeSession?.id
                            Surface(
                                color = if (isSelected) Indigo500.copy(alpha = 0.25f) else Slate800.copy(alpha = 0.6f),
                                shape = RoundedCornerShape(10.dp),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        viewModel.selectSession(sess.id)
                                        scope.launch { drawerState.close() }
                                    }
                            ) {
                                Row(
                                    modifier = Modifier.padding(10.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.SpaceBetween
                                ) {
                                    Column(modifier = Modifier.weight(1f)) {
                                        Text(
                                            text = sess.title,
                                            style = MaterialTheme.typography.bodyMedium,
                                            fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis
                                        )
                                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                            Text(
                                                text = if (sess.mode == "agent") "🤖 Agente" else "✨ Modelo",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = Sky400
                                            )
                                            Text(
                                                text = "• ${sess.turnCount} turnos",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = Slate400
                                            )
                                        }
                                        if (sess.environmentId.isNotBlank()) {
                                            Text(
                                                text = "📦 ${sess.environmentId.take(18)}",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = Emerald400,
                                                fontFamily = FontFamily.Monospace
                                            )
                                        }
                                    }
                                    IconButton(
                                        onClick = { viewModel.deleteSession(sess.id) },
                                        modifier = Modifier.size(28.dp)
                                    ) {
                                        Icon(
                                            Icons.Default.Delete,
                                            contentDescription = "Excluir sessão",
                                            tint = Slate400,
                                            modifier = Modifier.size(16.dp)
                                        )
                                    }
                                }
                            }
                        }
                    }

                    HorizontalDivider(color = Slate800)

                    OutlinedButton(
                        onClick = {
                            showApiKeyDialog = true
                            scope.launch { drawerState.close() }
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Icon(
                            Icons.Default.Key,
                            contentDescription = null,
                            tint = if (uiState.hasApiKey) Emerald400 else Amber400,
                            modifier = Modifier.size(16.dp)
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            if (uiState.hasApiKey) "API Key (${uiState.maskedApiKey})" else "Configurar API Key",
                            fontSize = 12.sp
                        )
                    }
                }
            }
        }
    ) {
        Scaffold(
            topBar = {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Slate900
                    ),
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Default.Menu, contentDescription = "Abrir menu")
                        }
                    },
                    title = {
                        Column {
                            Text(
                                text = activeSession?.title ?: "Antigravity Studio",
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.Bold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                            val envLabel = activeSession?.environmentId?.ifBlank { "remote" } ?: "remote"
                            Text(
                                text = "${if (activeSession?.mode == "agent") "🤖 ${activeSession.agentName}" else "✨ ${activeSession?.modelName ?: ""}"} • 📦 $envLabel",
                                style = MaterialTheme.typography.labelSmall,
                                color = Sky400,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    },
                    actions = {
                        IconButton(onClick = { showSandboxSheet = true }) {
                            Icon(
                                Icons.Default.FolderOpen,
                                contentDescription = "Ambiente Sandbox & Arquivos",
                                tint = if (!activeSession?.environmentId.isNullOrBlank()) Emerald400 else Color.White
                            )
                        }
                        IconButton(onClick = { showRunSettings = true }) {
                            Icon(Icons.Default.Settings, contentDescription = "Run Settings")
                        }
                        IconButton(onClick = { showCodeDialog = true }) {
                            Icon(Icons.Default.Code, contentDescription = "Get Code / Exportar Markdown")
                        }
                        IconButton(onClick = { showApiKeyDialog = true }) {
                            Icon(
                                Icons.Default.Key,
                                contentDescription = "API Key",
                                tint = if (uiState.hasApiKey) Emerald400 else Amber400
                            )
                        }
                    }
                )
            },
            bottomBar = {
                Surface(
                    color = Slate900,
                    tonalElevation = 6.dp,
                    modifier = Modifier
                        .fillMaxWidth()
                        .navigationBarsPadding()
                        .imePadding()
                ) {
                    Column(
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        // Barra de status de encadeamento (previous_interaction_id + background switch)
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            val effPrevId = uiState.effectivePreviousInteractionId
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.weight(1f)
                            ) {
                                Text(
                                    text = if (effPrevId.isNotBlank()) "🔗 Encadeado em: ${effPrevId.take(18)}..." else "🆕 Turno #1 (Novo)",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = if (uiState.customPrevInteractionId != null) Amber400 else Sky400,
                                    fontFamily = FontFamily.Monospace,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                                if (uiState.customPrevInteractionId != null) {
                                    IconButton(
                                        onClick = { viewModel.setCustomPrevInteractionId(null) },
                                        modifier = Modifier.size(20.dp)
                                    ) {
                                        Icon(
                                            Icons.Default.Close,
                                            contentDescription = "Restaurar último ID padrão",
                                            modifier = Modifier.size(14.dp)
                                        )
                                    }
                                }
                            }

                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("⚡ Background", style = MaterialTheme.typography.labelSmall, color = Slate400)
                                Spacer(Modifier.width(6.dp))
                                Switch(
                                    checked = uiState.backgroundMode,
                                    onCheckedChange = { viewModel.setBackgroundMode(it) }
                                )
                            }
                        }

                        // Campo de mensagem + botões de anexo e envio
                        Row(
                            verticalAlignment = Alignment.Bottom,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            IconButton(
                                onClick = { showSandboxSheet = true },
                                modifier = Modifier.size(42.dp)
                            ) {
                                Icon(
                                    Icons.Default.AttachFile,
                                    contentDescription = "Enviar arquivos para o Sandbox",
                                    tint = Sky400
                                )
                            }

                            OutlinedTextField(
                                value = promptInput,
                                onValueChange = { promptInput = it },
                                placeholder = { Text("Digite seu prompt para o Agente Antigravity...") },
                                minLines = 1,
                                maxLines = 5,
                                modifier = Modifier.weight(1f)
                            )

                            Button(
                                enabled = !uiState.isSending && promptInput.isNotBlank(),
                                onClick = {
                                    val toSend = promptInput
                                    promptInput = ""
                                    viewModel.sendMessage(toSend)
                                },
                                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 12.dp)
                            ) {
                                if (uiState.isSending) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        color = Color.White,
                                        strokeWidth = 2.dp
                                    )
                                } else {
                                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Enviar")
                                }
                            }
                        }
                    }
                }
            }
        ) { innerPadding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Slate950)
                    .padding(innerPadding)
            ) {
                // Banner de aviso de erro ou status
                AnimatedVisibility(visible = uiState.errorMessage != null || uiState.statusMessage != null) {
                    val isError = uiState.errorMessage != null
                    val bannerText = uiState.errorMessage ?: uiState.statusMessage.orEmpty()
                    Surface(
                        color = if (isError) Rose400.copy(alpha = 0.2f) else Emerald400.copy(alpha = 0.2f),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Text(
                                text = bannerText,
                                style = MaterialTheme.typography.bodySmall,
                                color = if (isError) Rose400 else Emerald400,
                                modifier = Modifier.weight(1f)
                            )
                            IconButton(
                                onClick = { viewModel.clearMessages() },
                                modifier = Modifier.size(22.dp)
                            ) {
                                Icon(Icons.Default.Close, contentDescription = "Fechar aviso", modifier = Modifier.size(14.dp))
                            }
                        }
                    }
                }

                val turns = activeSession?.turns.orEmpty()
                if (turns.isEmpty()) {
                    val linkedPrevId = uiState.effectivePreviousInteractionId
                    // Estado vazio com atalhos rápidos
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(24.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(12.dp)
                        ) {
                            Text(
                                "🚀 Antigravity Studio Mobile",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.Bold
                            )
                            Text(
                                "Inicie uma nova conversa com o Agente Antigravity, monte repositórios GitHub/GCS em Run Settings ou importe uma sessão existente via previous_interaction_id.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = Slate400
                            )
                            if (linkedPrevId.isNotBlank()) {
                                Surface(
                                    color = Sky400.copy(alpha = 0.12f),
                                    shape = RoundedCornerShape(12.dp),
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Column(
                                        modifier = Modifier.padding(12.dp),
                                        verticalArrangement = Arrangement.spacedBy(8.dp)
                                    ) {
                                        Text(
                                            "✅ Sessão vinculada a um previous_interaction_id:",
                                            style = MaterialTheme.typography.labelMedium,
                                            color = Sky400,
                                            fontWeight = FontWeight.Bold
                                        )
                                        Text(
                                            linkedPrevId,
                                            style = MaterialTheme.typography.bodySmall,
                                            fontFamily = FontFamily.Monospace,
                                            color = Color.White
                                        )
                                        Button(
                                            enabled = !uiState.isImporting,
                                            onClick = { viewModel.loadPreviousConversationIntoActiveSession(linkedPrevId) }
                                        ) {
                                            Icon(Icons.Default.Refresh, contentDescription = null, modifier = Modifier.size(16.dp))
                                            Spacer(Modifier.width(6.dp))
                                            Text(if (uiState.isImporting) "Carregando conversa..." else "Carregar conversa anterior na tela")
                                        }
                                    }
                                }
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                OutlinedButton(onClick = { showImportDialog = true }) {
                                    Icon(Icons.Default.Link, contentDescription = null, modifier = Modifier.size(16.dp))
                                    Spacer(Modifier.width(6.dp))
                                    Text("Importar por ID")
                                }
                                OutlinedButton(onClick = { showSandboxSheet = true }) {
                                    Icon(Icons.Default.FolderOpen, contentDescription = null, modifier = Modifier.size(16.dp))
                                    Spacer(Modifier.width(6.dp))
                                    Text("Upload Sandbox")
                                }
                            }
                        }
                    }
                } else {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(12.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        items(turns, key = { it.interactionId }) { turn ->
                            TurnCard(
                                turn = turn,
                                onFork = { viewModel.forkFromTurn(turn.interactionId) },
                                onPointIdHere = { viewModel.setCustomPrevInteractionId(turn.interactionId) },
                                onContinueIncomplete = {
                                    viewModel.setCustomPrevInteractionId(turn.interactionId)
                                    viewModel.sendMessage("continue")
                                },
                                onPoll = { viewModel.pollTurnManually(turn.interactionId) },
                                onCancel = { viewModel.cancelTurn(turn.interactionId) }
                            )
                        }
                    }
                }
            }
        }
    }

    if (showApiKeyDialog) {
        ApiKeyDialog(
            currentKey = uiState.apiKey,
            maskedKey = uiState.maskedApiKey,
            onDismiss = { showApiKeyDialog = false },
            onSave = { viewModel.saveApiKey(it) }
        )
    }

    if (showImportDialog) {
        ImportByIdDialog(
            isImporting = uiState.isImporting,
            onDismiss = { showImportDialog = false },
            onImport = { intId, envId, title, fetchHist ->
                viewModel.importChainById(
                    interactionId = intId,
                    environmentId = envId,
                    title = title,
                    fetchHistory = fetchHist
                )
            }
        )
    }

    if (showRunSettings && activeSession != null) {
        RunSettingsSheet(
            session = activeSession,
            onDismiss = { showRunSettings = false },
            onSave = { title, mode, agentName, modelName, envId, lastId, maxTok, sysInst, sources ->
                viewModel.updateActiveSessionSettings(
                    title = title,
                    mode = mode,
                    agentName = agentName,
                    modelName = modelName,
                    environmentId = envId,
                    lastInteractionId = lastId,
                    maxTotalTokens = maxTok,
                    systemInstruction = sysInst,
                    sources = sources
                )
            }
        )
    }

    if (showSandboxSheet && activeSession != null) {
        SandboxManagerSheet(
            session = activeSession,
            isUploading = uiState.isUploading,
            isListingFiles = uiState.isListingFiles,
            isDownloadingSnapshot = uiState.isDownloadingSnapshot,
            envCurrentPath = uiState.envCurrentPath,
            envFiles = uiState.envFiles,
            onDismiss = { showSandboxSheet = false },
            onProvisionEnvironment = { viewModel.provisionEnvironmentForActiveSession() },
            onUploadFiles = { uris, target, overwrite, extract, packTar, resumable ->
                viewModel.uploadUrisToEnvironment(
                    context = context,
                    uris = uris,
                    targetPath = target,
                    overwrite = overwrite,
                    extract = extract,
                    packAsTarGz = packTar,
                    resumable = resumable
                )
            },
            onUploadFolder = { treeUri, target, overwrite, resumable ->
                viewModel.uploadFolderTreeToEnvironment(
                    context = context,
                    treeUri = treeUri,
                    targetPath = target,
                    overwrite = overwrite,
                    resumable = resumable
                )
            },
            onListFiles = { path -> viewModel.loadEnvironmentFiles(path) },
            onDownloadFileOrSnapshot = { path ->
                viewModel.downloadAndShareEnvironmentFileOrSnapshot(context, path)
            }
        )
    }

    if (showCodeDialog && activeSession != null) {
        CodeAndExportDialog(
            session = activeSession,
            activePrevId = uiState.effectivePreviousInteractionId,
            onDismiss = { showCodeDialog = false },
            onShareMarkdown = { viewModel.exportAndShareSessionMarkdown(context) }
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TurnCard(
    turn: TurnModel,
    onFork: () -> Unit,
    onPointIdHere: () -> Unit,
    onContinueIncomplete: () -> Unit,
    onPoll: () -> Unit,
    onCancel: () -> Unit
) {
    val clipboard = LocalClipboardManager.current
    var showThoughts by remember { mutableStateOf(false) }
    var showTools by remember { mutableStateOf(false) }

    val thoughts = remember(turn.steps) {
        turn.steps.filter { it.type == "thought" && it.textContent.isNotBlank() }
    }
    val toolCalls = remember(turn.steps) {
        turn.steps.filter {
            it.type in listOf("function_call", "code_execution_call", "google_search_call")
        }
    }

    val statusColor = when (turn.status) {
        "completed" -> Emerald400
        "in_progress" -> Sky400
        "incomplete" -> Amber400
        else -> Rose400
    }

    Card(
        colors = CardDefaults.cardColors(containerColor = Slate900),
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // Cabeçalho do Turno com metadados de IDs e Status
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Surface(
                        color = Indigo500.copy(alpha = 0.25f),
                        shape = RoundedCornerShape(6.dp)
                    ) {
                        Text(
                            text = "Turno #${turn.turnIndex}",
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.Bold,
                            color = Sky400,
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                        )
                    }
                    Surface(
                        color = statusColor.copy(alpha = 0.2f),
                        shape = RoundedCornerShape(6.dp)
                    ) {
                        Text(
                            text = turn.status.uppercase(),
                            style = MaterialTheme.typography.labelSmall,
                            color = statusColor,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 3.dp)
                        )
                    }
                }

                if (turn.usage.totalTokens > 0) {
                    Text(
                        text = "${turn.usage.totalTokens} toks",
                        style = MaterialTheme.typography.labelSmall,
                        color = Slate400,
                        fontFamily = FontFamily.Monospace
                    )
                }
            }

            // Chips de IDs (clicáveis para copiar)
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Surface(
                    color = Slate800,
                    shape = RoundedCornerShape(6.dp),
                    modifier = Modifier.clickable {
                        clipboard.setText(AnnotatedString(turn.interactionId))
                    }
                ) {
                    Text(
                        text = "ID: ${turn.interactionId.take(18)}...",
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = Slate400,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 3.dp)
                    )
                }

                if (turn.previousInteractionId.isNotBlank()) {
                    Surface(
                        color = Slate800,
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.clickable {
                            clipboard.setText(AnnotatedString(turn.previousInteractionId))
                        }
                    ) {
                        Text(
                            text = "Prev: ${turn.previousInteractionId.take(14)}...",
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = Slate400,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 3.dp)
                        )
                    }
                }

                if (turn.environmentId.isNotBlank()) {
                    Surface(
                        color = Slate800,
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.clickable {
                            clipboard.setText(AnnotatedString(turn.environmentId))
                        }
                    ) {
                        Text(
                            text = "📦 ${turn.environmentId.take(16)}",
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = Emerald400,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 3.dp)
                        )
                    }
                }
            }

            // Prompt do Usuário
            Surface(
                color = Slate800.copy(alpha = 0.7f),
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(10.dp)) {
                    Text(
                        "👤 Você",
                        style = MaterialTheme.typography.labelSmall,
                        color = Sky400,
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(Modifier.height(4.dp))
                    SelectionContainer {
                        Text(
                            text = turn.userInputText.ifBlank { "(Sem entrada textual)" },
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }

            // Seção colapsável de Pensamentos (Thoughts)
            if (thoughts.isNotEmpty()) {
                Surface(
                    color = Slate800.copy(alpha = 0.45f),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { showThoughts = !showThoughts }
                        ) {
                            Text(
                                "💭 Pensamentos do Agente (${thoughts.size})",
                                style = MaterialTheme.typography.labelMedium,
                                color = Amber400
                            )
                            Icon(
                                imageVector = if (showThoughts) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                                contentDescription = null,
                                tint = Amber400
                            )
                        }
                        AnimatedVisibility(visible = showThoughts) {
                            Column(
                                modifier = Modifier.padding(top = 8.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                thoughts.forEach { t ->
                                    SelectionContainer {
                                        Text(
                                            text = t.textContent,
                                            style = MaterialTheme.typography.bodySmall,
                                            color = Slate400,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Seção colapsável de Chamadas de Ferramentas (Tool Calls)
            if (toolCalls.isNotEmpty()) {
                Surface(
                    color = Slate800.copy(alpha = 0.45f),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { showTools = !showTools }
                        ) {
                            Text(
                                "🔧 Execuções de Ferramentas (${toolCalls.size})",
                                style = MaterialTheme.typography.labelMedium,
                                color = Sky400
                            )
                            Icon(
                                imageVector = if (showTools) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                                contentDescription = null,
                                tint = Sky400
                            )
                        }
                        AnimatedVisibility(visible = showTools) {
                            Column(
                                modifier = Modifier.padding(top = 8.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                toolCalls.forEach { tc ->
                                    Surface(
                                        color = Slate950,
                                        shape = RoundedCornerShape(6.dp),
                                        modifier = Modifier.fillMaxWidth()
                                    ) {
                                        Column(modifier = Modifier.padding(8.dp)) {
                                            Text(
                                                text = "Tool: ${tc.name}",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = Emerald400,
                                                fontFamily = FontFamily.Monospace
                                            )
                                            if (tc.argumentsJson.isNotBlank()) {
                                                Text(
                                                    text = tc.argumentsJson.take(500),
                                                    style = MaterialTheme.typography.bodySmall,
                                                    fontFamily = FontFamily.Monospace,
                                                    fontSize = 11.sp,
                                                    color = Slate400
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Resposta do Agente / Modelo
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    "🤖 Antigravity / Gemini",
                    style = MaterialTheme.typography.labelSmall,
                    color = Emerald400,
                    fontWeight = FontWeight.SemiBold
                )
                if (turn.status == "in_progress" && turn.outputText.isBlank()) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        modifier = Modifier.padding(vertical = 8.dp)
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                        Text(
                            "Executando no sandbox em background... (atualizando automaticamente)",
                            style = MaterialTheme.typography.bodySmall,
                            color = Sky400
                        )
                    }
                } else {
                    SelectionContainer {
                        Text(
                            text = turn.outputText.ifBlank { "(Sem resposta textual final neste turno)" },
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }

            HorizontalDivider(color = Slate800)

            // Barra de Ações do Turno (Fork, Apontar ID, Copiar, Continuar Incompleto, Cancelar)
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                AssistChip(
                    onClick = onFork,
                    leadingIcon = {
                        Icon(Icons.Default.CallSplit, contentDescription = null, modifier = Modifier.size(15.dp))
                    },
                    label = { Text("Ramificar (Fork)", fontSize = 11.sp) }
                )

                AssistChip(
                    onClick = onPointIdHere,
                    leadingIcon = {
                        Icon(Icons.Default.MyLocation, contentDescription = null, modifier = Modifier.size(15.dp))
                    },
                    label = { Text("Apontar ID aqui", fontSize = 11.sp) }
                )

                AssistChip(
                    onClick = { clipboard.setText(AnnotatedString(turn.outputText)) },
                    leadingIcon = {
                        Icon(Icons.Default.ContentCopy, contentDescription = null, modifier = Modifier.size(15.dp))
                    },
                    label = { Text("Copiar", fontSize = 11.sp) }
                )

                if (turn.status == "incomplete") {
                    AssistChip(
                        onClick = onContinueIncomplete,
                        leadingIcon = {
                            Icon(
                                Icons.Default.PlayArrow,
                                contentDescription = null,
                                tint = Emerald400,
                                modifier = Modifier.size(16.dp)
                            )
                        },
                        label = { Text("Continuar de onde parou", fontSize = 11.sp, color = Emerald400) }
                    )
                }

                if (turn.status == "in_progress") {
                    AssistChip(
                        onClick = onPoll,
                        leadingIcon = {
                            Icon(Icons.Default.Refresh, contentDescription = null, modifier = Modifier.size(15.dp))
                        },
                        label = { Text("Atualizar", fontSize = 11.sp) }
                    )
                    AssistChip(
                        onClick = onCancel,
                        leadingIcon = {
                            Icon(
                                Icons.Default.Cancel,
                                contentDescription = null,
                                tint = Rose400,
                                modifier = Modifier.size(15.dp)
                            )
                        },
                        label = { Text("Cancelar", fontSize = 11.sp, color = Rose400) }
                    )
                }
            }
        }
    }
}
