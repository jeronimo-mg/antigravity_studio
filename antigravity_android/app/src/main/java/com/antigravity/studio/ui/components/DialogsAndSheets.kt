package com.antigravity.studio.ui.components

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.antigravity.studio.data.model.EnvFileEntry
import com.antigravity.studio.data.model.SessionModel
import com.antigravity.studio.data.model.SourceItem
import com.antigravity.studio.data.remote.InteractionsClient
import com.antigravity.studio.ui.theme.Emerald400
import com.antigravity.studio.ui.theme.Sky400
import com.antigravity.studio.ui.theme.Slate800

@Composable
fun ApiKeyDialog(
    currentKey: String,
    maskedKey: String,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit
) {
    var keyInput by remember { mutableStateOf(currentKey) }
    var showRaw by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("🔑 Configurar Gemini API Key", fontWeight = FontWeight.Bold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    "Sua chave é armazenada apenas localmente no banco SQLite do seu aparelho Android e enviada diretamente para generativelanguage.googleapis.com.",
                    style = MaterialTheme.typography.bodySmall
                )
                if (maskedKey.isNotBlank()) {
                    Text(
                        "Chave ativa: $maskedKey",
                        color = Emerald400,
                        style = MaterialTheme.typography.labelMedium
                    )
                }
                OutlinedTextField(
                    value = keyInput,
                    onValueChange = { keyInput = it },
                    label = { Text("GEMINI_API_KEY (AIzaSy...)") },
                    singleLine = true,
                    visualTransformation = if (showRaw) VisualTransformation.None else PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth()
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = showRaw, onCheckedChange = { showRaw = it })
                    Text("Mostrar caracteres da chave", style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            Button(onClick = {
                onSave(keyInput)
                onDismiss()
            }) {
                Text("Salvar Chave")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancelar")
            }
        }
    )
}

@Composable
fun ImportByIdDialog(
    isImporting: Boolean,
    onDismiss: () -> Unit,
    onImport: (
        interactionId: String,
        environmentId: String,
        title: String,
        fetchHistory: Boolean
    ) -> Unit
) {
    var interactionId by remember { mutableStateOf("") }
    var environmentId by remember { mutableStateOf("") }
    var title by remember { mutableStateOf("") }
    var fetchHistory by remember { mutableStateOf(true) }

    AlertDialog(
        onDismissRequest = { if (!isImporting) onDismiss() },
        title = { Text("🔗 Importar / Continuar por ID", fontWeight = FontWeight.Bold) },
        text = {
            Column(
                verticalArrangement = Arrangement.spacedBy(10.dp),
                modifier = Modifier.verticalScroll(rememberScrollState())
            ) {
                Text(
                    "Cole o ID da última interação (v1_...). O aplicativo percorre recursivamente toda a cadeia de previous_interaction_id e reconstrói todos os turnos.",
                    style = MaterialTheme.typography.bodySmall
                )
                OutlinedTextField(
                    value = interactionId,
                    onValueChange = { interactionId = it },
                    label = { Text("Interaction ID (v1_...)*") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = environmentId,
                    onValueChange = { environmentId = it },
                    label = { Text("Environment ID opcional (env_...)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = title,
                    onValueChange = { title = it },
                    label = { Text("Título opcional da sessão") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Switch(checked = fetchHistory, onCheckedChange = { fetchHistory = it })
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "Recuperar histórico completo de turnos na API (include_input=true)",
                        style = MaterialTheme.typography.bodySmall
                    )
                }
                if (isImporting) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                        Text("Percorrendo cadeia de interações...", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        },
        confirmButton = {
            Button(
                enabled = !isImporting && interactionId.isNotBlank(),
                onClick = {
                    onImport(interactionId, environmentId, title, fetchHistory)
                    onDismiss()
                }
            ) {
                Text("Importar")
            }
        },
        dismissButton = {
            TextButton(enabled = !isImporting, onClick = onDismiss) {
                Text("Cancelar")
            }
        }
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun RunSettingsSheet(
    session: SessionModel,
    onDismiss: () -> Unit,
    onSave: (
        title: String,
        mode: String,
        agentName: String,
        modelName: String,
        environmentId: String,
        lastInteractionId: String,
        maxTotalTokens: Int,
        systemInstruction: String,
        sources: List<SourceItem>
    ) -> Unit
) {
    var title by remember { mutableStateOf(session.title) }
    var mode by remember { mutableStateOf(session.mode) }
    var agentName by remember { mutableStateOf(session.agentName) }
    var modelName by remember { mutableStateOf(session.modelName) }
    var environmentId by remember { mutableStateOf(session.environmentId) }
    var lastInteractionId by remember { mutableStateOf(session.lastInteractionId) }
    var maxTokensStr by remember {
        mutableStateOf(if (session.maxTotalTokens > 0) session.maxTotalTokens.toString() else "")
    }
    var systemInstruction by remember { mutableStateOf(session.systemInstruction) }
    val sources = remember { mutableStateListOf<SourceItem>().apply { addAll(session.sources) } }

    // Novo Source form
    var newSourceType by remember { mutableStateOf("repository") }
    var newSourceUri by remember { mutableStateOf("") }
    var newSourceTarget by remember { mutableStateOf("/workspace") }
    var newSourceContent by remember { mutableStateOf("") }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .padding(bottom = 28.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text(
                "⚙️ Run Settings (Configurações da Sessão)",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold
            )

            OutlinedTextField(
                value = title,
                onValueChange = { title = it },
                label = { Text("Título da Conversa") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = mode == "agent",
                    onClick = { mode = "agent" },
                    label = { Text("🤖 Modo Agente (Antigravity)") }
                )
                FilterChip(
                    selected = mode == "model",
                    onClick = { mode = "model" },
                    label = { Text("✨ Modo Modelo (Gemini)") }
                )
            }

            if (mode == "agent") {
                OutlinedTextField(
                    value = agentName,
                    onValueChange = { agentName = it },
                    label = { Text("Agent Name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
            }

            OutlinedTextField(
                value = modelName,
                onValueChange = { modelName = it },
                label = { Text("Model Name") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )

            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("gemini-3.8-flash", "gemini-2.5-pro", "gemini-2.5-flash").forEach { preset ->
                    AssistChip(
                        onClick = { modelName = preset },
                        label = { Text(preset, fontSize = 11.sp) }
                    )
                }
            }

            OutlinedTextField(
                value = environmentId,
                onValueChange = { environmentId = it },
                label = { Text("Environment ID (vazio = 'remote' novo)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )

            OutlinedTextField(
                value = lastInteractionId,
                onValueChange = { lastInteractionId = it },
                label = { Text("Último Interaction ID (previous_interaction_id)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )

            if (mode == "agent") {
                OutlinedTextField(
                    value = maxTokensStr,
                    onValueChange = { maxTokensStr = it.filter { c -> c.isDigit() } },
                    label = { Text("Max Total Tokens (0 ou vazio = padrão)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
            } else {
                OutlinedTextField(
                    value = systemInstruction,
                    onValueChange = { systemInstruction = it },
                    label = { Text("System Instruction") },
                    minLines = 2,
                    maxLines = 5,
                    modifier = Modifier.fillMaxWidth()
                )
            }

            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
            Text(
                "📂 Fontes Montadas no Sandbox (sources)",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold
            )

            sources.forEachIndexed { idx, src ->
                Surface(
                    color = Slate800,
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier.padding(10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                "[${src.type.uppercase()}] -> ${src.target}",
                                style = MaterialTheme.typography.labelMedium,
                                color = Sky400
                            )
                            Text(
                                if (src.type == "inline") "Conteúdo inline (${src.content.length} chars)" else src.source,
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                        IconButton(onClick = { sources.removeAt(idx) }) {
                            Icon(Icons.Default.Delete, contentDescription = "Remover fonte")
                        }
                    }
                }
            }

            // Adicionar nova fonte
            Surface(
                color = Slate800.copy(alpha = 0.5f),
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(10.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text("＋ Adicionar Fonte (GitHub, GCS ou Inline)", style = MaterialTheme.typography.labelMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("repository" to "Git Repo", "gcs" to "GCS Bucket", "inline" to "Inline").forEach { (t, label) ->
                            FilterChip(
                                selected = newSourceType == t,
                                onClick = { newSourceType = t },
                                label = { Text(label, fontSize = 11.sp) }
                            )
                        }
                    }
                    if (newSourceType != "inline") {
                        OutlinedTextField(
                            value = newSourceUri,
                            onValueChange = { newSourceUri = it },
                            label = {
                                Text(if (newSourceType == "repository") "URL Git (https://github.com/...)" else "URI GCS (gs://bucket/pasta)")
                            },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                    } else {
                        OutlinedTextField(
                            value = newSourceContent,
                            onValueChange = { newSourceContent = it },
                            label = { Text("Conteúdo de texto do arquivo") },
                            minLines = 2,
                            maxLines = 4,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
                    OutlinedTextField(
                        value = newSourceTarget,
                        onValueChange = { newSourceTarget = it },
                        label = { Text("Caminho de destino no container (ex: /workspace/repo)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    OutlinedButton(
                        onClick = {
                            if (newSourceType == "inline" && newSourceTarget.isNotBlank()) {
                                sources.add(SourceItem("inline", "", newSourceTarget.trim(), newSourceContent))
                                newSourceContent = ""
                            } else if (newSourceUri.isNotBlank()) {
                                sources.add(SourceItem(newSourceType, newSourceUri.trim(), newSourceTarget.trim(), ""))
                                newSourceUri = ""
                            }
                        }
                    ) {
                        Icon(Icons.Default.Add, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Incluir na lista de Sources")
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End
            ) {
                TextButton(onClick = onDismiss) { Text("Cancelar") }
                Spacer(Modifier.width(8.dp))
                Button(onClick = {
                    onSave(
                        title,
                        mode,
                        agentName,
                        modelName,
                        environmentId,
                        lastInteractionId,
                        maxTokensStr.toIntOrNull() ?: 0,
                        systemInstruction,
                        sources.toList()
                    )
                    onDismiss()
                }) {
                    Text("Salvar Configurações")
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SandboxManagerSheet(
    session: SessionModel,
    isUploading: Boolean,
    isListingFiles: Boolean,
    isDownloadingSnapshot: Boolean,
    envCurrentPath: String,
    envFiles: List<EnvFileEntry>,
    onDismiss: () -> Unit,
    onProvisionEnvironment: () -> Unit,
    onUploadFiles: (List<Uri>, String, Boolean, Boolean, Boolean, Boolean) -> Unit,
    onUploadFolder: (Uri, String, Boolean, Boolean) -> Unit,
    onListFiles: (String) -> Unit,
    onDownloadFileOrSnapshot: (String?) -> Unit
) {
    val context = LocalContext.current
    var targetPath by remember { mutableStateOf("/workspace") }
    var overwrite by remember { mutableStateOf(true) }
    var extractArchive by remember { mutableStateOf(false) }
    var packAsTarGz by remember { mutableStateOf(false) }
    var resumable by remember { mutableStateOf(false) }
    var browsePath by remember { mutableStateOf(envCurrentPath.ifBlank { "workspace" }) }

    val filesPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenMultipleDocuments()
    ) { uris ->
        if (uris.isNotEmpty()) {
            onUploadFiles(uris, targetPath, overwrite, extractArchive, packAsTarGz, resumable)
        }
    }

    val folderPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocumentTree()
    ) { treeUri ->
        if (treeUri != null) {
            onUploadFolder(treeUri, targetPath, overwrite, resumable)
        }
    }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .padding(bottom = 28.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text(
                "📦 Ambiente Sandbox (Environments API)",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold
            )

            Surface(
                color = Slate800,
                shape = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier.padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    val activeEnv = session.environmentId.ifBlank { "remote (ainda não provisionado)" }
                    Text(
                        "Environment ID: $activeEnv",
                        style = MaterialTheme.typography.labelLarge,
                        color = if (session.environmentId.isNotBlank()) Emerald400 else Sky400,
                        fontFamily = FontFamily.Monospace
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            enabled = !isUploading,
                            onClick = onProvisionEnvironment,
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("⚡ Provisionar Novo", fontSize = 12.sp)
                        }
                        Button(
                            enabled = !isDownloadingSnapshot && session.environmentId.isNotBlank(),
                            onClick = { onDownloadFileOrSnapshot(null) },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.CloudDownload, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Snapshot .tar.gz", fontSize = 12.sp)
                        }
                    }
                }
            }

            // Upload Section
            Text(
                "⬆️ Upload Direto de Arquivos ou Pastas do Celular",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold
            )

            OutlinedTextField(
                value = targetPath,
                onValueChange = { targetPath = it },
                label = { Text("Destino no Container (ex: /workspace ou /workspace/dados)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )

            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = packAsTarGz, onCheckedChange = { packAsTarGz = it })
                Text("Empacotar em .tar.gz + extrair no destino (extract=true)", style = MaterialTheme.typography.bodySmall)
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = resumable, onCheckedChange = { resumable = it })
                Text("Usar protocolo Resumable (uploadType=resumable)", style = MaterialTheme.typography.bodySmall)
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    enabled = !isUploading,
                    onClick = { filesPicker.launch(arrayOf("*/*")) },
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.CloudUpload, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Enviar Arquivo(s)", fontSize = 12.sp)
                }
                OutlinedButton(
                    enabled = !isUploading,
                    onClick = { folderPicker.launch(null) },
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.CreateNewFolder, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Enviar Pasta (.tar.gz)", fontSize = 12.sp)
                }
            }

            if (isUploading || isDownloadingSnapshot) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text(
                        if (isUploading) "Transferindo para o sandbox remoto..." else "Baixando e empacotando snapshot...",
                        style = MaterialTheme.typography.bodySmall
                    )
                }
            }

            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))

            // Explorador de arquivos do sandbox
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    "🗂️ Explorador de Arquivos do Sandbox",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold
                )
                IconButton(
                    enabled = !isListingFiles && session.environmentId.isNotBlank(),
                    onClick = { onListFiles(browsePath) }
                ) {
                    Icon(Icons.Default.Refresh, contentDescription = "Listar arquivos")
                }
            }

            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                OutlinedTextField(
                    value = browsePath,
                    onValueChange = { browsePath = it },
                    label = { Text("Diretório (ex: workspace)") },
                    singleLine = true,
                    modifier = Modifier.weight(1f)
                )
                OutlinedButton(
                    enabled = !isListingFiles && session.environmentId.isNotBlank(),
                    onClick = { onListFiles(browsePath) }
                ) {
                    Text("Listar")
                }
            }

            if (isListingFiles) {
                CircularProgressIndicator(modifier = Modifier.size(22.dp), strokeWidth = 2.dp)
            } else if (envFiles.isNotEmpty()) {
                LazyColumn(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 240.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    items(envFiles) { entry ->
                        Surface(
                            color = Slate800,
                            shape = RoundedCornerShape(8.dp),
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable {
                                    if (entry.isDirectory) {
                                        browsePath = entry.path
                                        onListFiles(entry.path)
                                    } else {
                                        onDownloadFileOrSnapshot(entry.path)
                                    }
                                }
                        ) {
                            Row(
                                modifier = Modifier.padding(10.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    modifier = Modifier.weight(1f)
                                ) {
                                    Icon(
                                        imageVector = if (entry.isDirectory) Icons.Default.Folder else Icons.Default.Description,
                                        contentDescription = null,
                                        tint = if (entry.isDirectory) Sky400 else Emerald400,
                                        modifier = Modifier.size(18.dp)
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    Column {
                                        Text(
                                            entry.path,
                                            style = MaterialTheme.typography.bodySmall,
                                            fontFamily = FontFamily.Monospace,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis
                                        )
                                        if (!entry.isDirectory && entry.sizeBytes > 0) {
                                            Text(
                                                "${entry.sizeBytes} bytes",
                                                style = MaterialTheme.typography.labelSmall
                                            )
                                        }
                                    }
                                }
                                IconButton(onClick = { onDownloadFileOrSnapshot(entry.path) }) {
                                    Icon(
                                        Icons.Default.CloudDownload,
                                        contentDescription = "Baixar",
                                        modifier = Modifier.size(18.dp)
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

@Composable
fun CodeAndExportDialog(
    session: SessionModel,
    activePrevId: String,
    onDismiss: () -> Unit,
    onShareMarkdown: () -> Unit
) {
    val clipboard = LocalClipboardManager.current
    var tabIndex by remember { mutableIntStateOf(0) }

    val pythonCode = remember(session, activePrevId) {
        InteractionsClient.generatePythonSnippet(session, activePrevId)
    }
    val curlCode = remember(session, activePrevId) {
        InteractionsClient.generateCurlSnippet(session, activePrevId)
    }
    val markdownText = remember(session) {
        InteractionsClient.formatSessionAsMarkdown(session)
    }

    val currentText = when (tabIndex) {
        0 -> pythonCode
        1 -> curlCode
        else -> markdownText
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("💻 Get Code & Exportar Markdown", fontWeight = FontWeight.Bold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                TabRow(selectedTabIndex = tabIndex) {
                    Tab(selected = tabIndex == 0, onClick = { tabIndex = 0 }, text = { Text("Python") })
                    Tab(selected = tabIndex == 1, onClick = { tabIndex = 1 }, text = { Text("cURL") })
                    Tab(selected = tabIndex == 2, onClick = { tabIndex = 2 }, text = { Text("Markdown") })
                }

                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(260.dp)
                        .background(Slate800, RoundedCornerShape(8.dp))
                        .padding(10.dp)
                        .verticalScroll(rememberScrollState())
                ) {
                    Text(
                        text = currentText,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp
                    )
                }

                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    OutlinedButton(
                        onClick = { clipboard.setText(AnnotatedString(currentText)) },
                        modifier = Modifier.weight(1f)
                    ) {
                        Icon(Icons.Default.ContentCopy, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Copiar", fontSize = 12.sp)
                    }
                    Button(
                        onClick = onShareMarkdown,
                        modifier = Modifier.weight(1f)
                    ) {
                        Icon(Icons.Default.Share, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Exportar .md", fontSize = 12.sp)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Fechar") }
        }
    )
}
