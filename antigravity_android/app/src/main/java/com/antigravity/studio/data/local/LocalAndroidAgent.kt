package com.antigravity.studio.data.local

import android.os.Build
import android.os.Environment
import android.os.StatFs
import com.antigravity.studio.data.model.TurnModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.TimeUnit

object LocalAndroidAgent {

    private const val INTERACTIONS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(45, TimeUnit.SECONDS)
            .readTimeout(180, TimeUnit.SECONDS)
            .writeTimeout(180, TimeUnit.SECONDS)
            .followRedirects(true)
            .followSslRedirects(true)
            .build()
    }

    fun getDefaultAndroidWorkdir(filesDir: File? = null): String {
        val base = filesDir ?: File(Environment.getExternalStorageDirectory(), "Download/AntigravityWorkspace")
        if (!base.exists()) {
            base.mkdirs()
        }
        return base.absolutePath
    }

    fun isLocalEnvironment(environmentId: String?): Boolean {
        return (environmentId ?: "").trim().startsWith("local:")
    }

    fun extractWorkdirFromEnvId(environmentId: String?, fallbackDir: String): String {
        val raw = (environmentId ?: "").trim()
        if (raw.startsWith("local:")) {
            val path = raw.removePrefix("local:").trim()
            if (path.isNotEmpty()) return path
        }
        return fallbackDir
    }

    private fun resolveLocalFile(pathStr: String, workdir: String): File {
        val clean = pathStr.trim().ifEmpty { "." }
        val f = File(clean)
        return if (f.isAbsolute) f.canonicalFile else File(workdir, clean).canonicalFile
    }

    fun getAndroidHardwareSummary(workdir: String): Map<String, String> {
        val info = linkedMapOf<String, String>()
        info["device"] = "${Build.MANUFACTURER} ${Build.MODEL} (${Build.DEVICE})"
        info["android_version"] = "Android ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})"
        info["kernel"] = "${System.getProperty("os.name") ?: "Linux"} ${System.getProperty("os.version") ?: ""}".trim()
        info["abis"] = Build.SUPPORTED_ABIS.joinToString(", ")
        info["cpu_cores"] = Runtime.getRuntime().availableProcessors().toString()
        try {
            val memFile = File("/proc/meminfo")
            if (memFile.exists() && memFile.canRead()) {
                val lines = memFile.readLines()
                val totalKb = lines.firstOrNull { it.startsWith("MemTotal:") }
                    ?.replace(Regex("[^0-9]"), "")?.toLongOrNull() ?: 0L
                val availKb = lines.firstOrNull { it.startsWith("MemAvailable:") }
                    ?.replace(Regex("[^0-9]"), "")?.toLongOrNull() ?: 0L
                if (totalKb > 0) {
                    info["ram_total_gb"] = String.format("%.2f GB", totalKb / (1024.0 * 1024.0))
                    info["ram_available_gb"] = String.format("%.2f GB", availKb / (1024.0 * 1024.0))
                }
            }
        } catch (_: Exception) {
        }
        try {
            val target = File(workdir).apply { if (!exists()) mkdirs() }
            val stat = StatFs(target.absolutePath)
            val totalGb = stat.totalBytes / (1024.0 * 1024.0 * 1024.0)
            val freeGb = stat.availableBytes / (1024.0 * 1024.0 * 1024.0)
            info["storage_total_gb"] = String.format("%.2f GB", totalGb)
            info["storage_free_gb"] = String.format("%.2f GB", freeGb)
        } catch (_: Exception) {
        }
        return info
    }

    fun buildSystemInstruction(workdir: String, customInstruction: String = ""): String {
        val hw = getAndroidHardwareSummary(workdir)
        val sb = StringBuilder()
        sb.appendLine("[DIRETIVA CRÍTICA DE MODO MÁQUINA LOCAL NO CELULAR ANDROID — LEIA COM ATENÇÃO]")
        sb.appendLine("Você está operando em MODO MÁQUINA LOCAL conectado diretamente ao smartphone/tablet Android real do usuário.")
        sb.appendLine("ATENÇÃO: Suas ferramentas nativas internas (`default_api:*`) rodam em um container remoto na nuvem e NÃO correspondem ao celular do usuário.")
        sb.appendLine("PORTANTO, É ESTRITAMENTE PROIBIDO chamar qualquer ferramenta interna `default_api:*` neste turno!")
        sb.appendLine()
        sb.appendLine("Dados Reais do Dispositivo Android Detectados Agora:")
        sb.appendLine("- Aparelho / Modelo: ${hw["device"]}")
        sb.appendLine("- Sistema Operacional: ${hw["android_version"]}")
        sb.appendLine("- Kernel / Arquitetura: ${hw["kernel"]} (${hw["abis"]})")
        sb.appendLine("- Núcleos de CPU: ${hw["cpu_cores"]}")
        if (hw.containsKey("ram_total_gb")) {
            sb.appendLine("- Memória RAM: ${hw["ram_total_gb"]} total (${hw["ram_available_gb"]} disponível)")
        }
        if (hw.containsKey("storage_total_gb")) {
            sb.appendLine("- Armazenamento Interno: ${hw["storage_total_gb"]} total (${hw["storage_free_gb"]} livre)")
        }
        sb.appendLine("- Diretório de Trabalho Local (`local_workdir`): $workdir")
        sb.appendLine()
        sb.appendLine("COMO EXECUTAR COMANDOS E ACESSAR ARQUIVOS NO CELULAR ANDROID DO USUÁRIO:")
        sb.appendLine("Sempre que precisar executar comandos no shell do Android (`/system/bin/sh`), ler/criar/editar arquivos locais ou listar pastas, responda APENAS com um ou mais blocos XML `<local_tool_call>` contendo JSON válido:")
        sb.appendLine("<local_tool_call>")
        sb.appendLine("{\"name\": \"execute_bash\", \"arguments\": {\"command\": \"uname -a && getprop ro.product.model\", \"Cwd\": \".\"}}")
        sb.appendLine("</local_tool_call>")
        sb.appendLine()
        sb.appendLine("Ferramentas Locais Disponíveis via `<local_tool_call>`:")
        sb.appendLine("1. `execute_bash`: `{\"name\": \"execute_bash\", \"arguments\": {\"command\": \"...\", \"Cwd\": \".\"}}`")
        sb.appendLine("2. `view_file`: `{\"name\": \"view_file\", \"arguments\": {\"AbsolutePath\": \"...\", \"StartLine\": 1, \"EndLine\": 200}}`")
        sb.appendLine("3. `write_to_file`: `{\"name\": \"write_to_file\", \"arguments\": {\"TargetFile\": \"...\", \"CodeContent\": \"...\", \"Overwrite\": true}}`")
        sb.appendLine("4. `replace_file_content`: `{\"name\": \"replace_file_content\", \"arguments\": {\"TargetFile\": \"...\", \"TargetContent\": \"...\", \"ReplacementContent\": \"...\"}}`")
        sb.appendLine("5. `list_dir`: `{\"name\": \"list_dir\", \"arguments\": {\"DirectoryPath\": \".\"}}`")
        sb.appendLine("6. `find_by_name`: `{\"name\": \"find_by_name\", \"arguments\": {\"SearchDirectory\": \".\", \"Pattern\": \"*\"}}`")
        sb.appendLine("7. `grep_search`: `{\"name\": \"grep_search\", \"arguments\": {\"SearchPath\": \".\", \"Query\": \"...\"}}`")
        sb.appendLine("8. `web_search` / `browse_url`: `{\"name\": \"web_search\", \"arguments\": {\"queries\": [\"...\"]}}` / `{\"name\": \"browse_url\", \"arguments\": {\"urls\": [\"...\"]}}`")
        if (customInstruction.isNotBlank()) {
            sb.appendLine()
            sb.appendLine("Instruções Adicionais do Usuário:")
            sb.appendLine(customInstruction.trim())
        }
        return sb.toString()
    }

    fun getToolsDeclaration(): JSONArray {
        val declarations = JSONArray()

        declarations.put(JSONObject().apply {
            put("name", "execute_bash")
            put("description", "Executes a shell command directly on the local Android device (/system/bin/sh) and returns stdout, stderr, and exit_code.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("command", JSONObject().put("type", "STRING").put("description", "Shell command to execute on the Android device."))
                    put("Cwd", JSONObject().put("type", "STRING").put("description", "Optional working directory."))
                })
                put("required", JSONArray().put("command"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "view_file")
            put("description", "View the contents of a file on the local Android filesystem with 1-indexed line numbers.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("AbsolutePath", JSONObject().put("type", "STRING").put("description", "Path to file to view."))
                    put("StartLine", JSONObject().put("type", "INTEGER").put("description", "Optional 1-indexed start line."))
                    put("EndLine", JSONObject().put("type", "INTEGER").put("description", "Optional 1-indexed end line."))
                })
                put("required", JSONArray().put("AbsolutePath"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "write_to_file")
            put("description", "Create or overwrite a file on the local Android filesystem.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("TargetFile", JSONObject().put("type", "STRING").put("description", "Path of the file to write."))
                    put("CodeContent", JSONObject().put("type", "STRING").put("description", "Content to write to the file."))
                    put("Overwrite", JSONObject().put("type", "BOOLEAN").put("description", "Whether to overwrite existing file."))
                })
                put("required", JSONArray().put("TargetFile").put("CodeContent"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "replace_file_content")
            put("description", "Replace a contiguous block of text in an existing local file.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("TargetFile", JSONObject().put("type", "STRING"))
                    put("TargetContent", JSONObject().put("type", "STRING"))
                    put("ReplacementContent", JSONObject().put("type", "STRING"))
                })
                put("required", JSONArray().put("TargetFile").put("TargetContent").put("ReplacementContent"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "list_dir")
            put("description", "List the contents of a directory on the local Android filesystem.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("DirectoryPath", JSONObject().put("type", "STRING").put("description", "Directory path to list."))
                })
                put("required", JSONArray().put("DirectoryPath"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "find_by_name")
            put("description", "Search for files and subdirectories within a local directory.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("SearchDirectory", JSONObject().put("type", "STRING"))
                    put("Pattern", JSONObject().put("type", "STRING"))
                })
                put("required", JSONArray().put("SearchDirectory"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "grep_search")
            put("description", "Search for a text pattern inside files in a local directory.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("SearchPath", JSONObject().put("type", "STRING"))
                    put("Query", JSONObject().put("type", "STRING"))
                })
                put("required", JSONArray().put("SearchPath").put("Query"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "web_search")
            put("description", "Search the web for relevant information.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("queries", JSONObject().put("type", "ARRAY").put("items", JSONObject().put("type", "STRING")))
                })
                put("required", JSONArray().put("queries"))
            })
        })

        declarations.put(JSONObject().apply {
            put("name", "browse_url")
            put("description", "Extract readable text content from URLs.")
            put("parameters", JSONObject().apply {
                put("type", "OBJECT")
                put("properties", JSONObject().apply {
                    put("urls", JSONObject().put("type", "ARRAY").put("items", JSONObject().put("type", "STRING")))
                })
                put("required", JSONArray().put("urls"))
            })
        })

        return JSONArray().put(JSONObject().put("functionDeclarations", declarations))
    }

    private fun executeBashTool(args: JSONObject, workdir: String): JSONObject {
        val cmd = args.optString("command", args.optString("cmd", "")).trim()
        val cwdArg = args.optString("Cwd", args.optString("cwd", "")).trim()
        val targetDir = if (cwdArg.isNotEmpty()) resolveLocalFile(cwdArg, workdir) else File(workdir)
        if (!targetDir.exists()) targetDir.mkdirs()

        if (cmd.isEmpty()) {
            return JSONObject().put("error", "Empty command").put("exit_code", 1)
        }
        val started = System.currentTimeMillis()
        return try {
            val pb = ProcessBuilder("/system/bin/sh", "-c", cmd)
            pb.directory(targetDir)
            val proc = pb.start()
            proc.outputStream.close() // Fecha stdin imediatamente para evitar bloqueio interativo
            val finished = proc.waitFor(60, TimeUnit.SECONDS)
            if (!finished) {
                proc.destroyForcibly()
                return JSONObject()
                    .put("command", cmd)
                    .put("exit_code", 124)
                    .put("error", "Command timed out after 60s")
            }
            val stdout = proc.inputStream.bufferedReader().readText().take(24000)
            val stderr = proc.errorStream.bufferedReader().readText().take(24000)
            JSONObject()
                .put("command", cmd)
                .put("cwd", targetDir.absolutePath)
                .put("exit_code", proc.exitValue())
                .put("duration_ms", System.currentTimeMillis() - started)
                .put("stdout", stdout)
                .put("stderr", stderr)
        } catch (e: Exception) {
            JSONObject()
                .put("command", cmd)
                .put("exit_code", 1)
                .put("error", e.message ?: "Error executing command")
        }
    }

    private fun viewFileTool(args: JSONObject, workdir: String): JSONObject {
        val pathStr = args.optString("AbsolutePath", args.optString("path", args.optString("TargetFile", "")))
        val f = resolveLocalFile(pathStr, workdir)
        if (!f.exists()) return JSONObject().put("error", "Arquivo não encontrado: ${f.absolutePath}")
        if (f.isDirectory) return JSONObject().put("error", "O caminho é um diretório, use list_dir: ${f.absolutePath}")
        return try {
            val lines = f.readLines()
            val start = args.optInt("StartLine", 1).coerceAtLeast(1)
            val end = args.optInt("EndLine", (start + 399).coerceAtMost(lines.size)).coerceAtMost(lines.size)
            val numbered = (start..end).mapNotNull { idx ->
                lines.getOrNull(idx - 1)?.let { "$idx: $it" }
            }.joinToString("\n")
            JSONObject()
                .put("path", f.absolutePath)
                .put("total_lines", lines.size)
                .put("content", numbered)
        } catch (e: Exception) {
            JSONObject().put("error", e.message ?: "Erro ao ler arquivo")
        }
    }

    private fun writeFileTool(args: JSONObject, workdir: String): JSONObject {
        val pathStr = args.optString("TargetFile", args.optString("path", ""))
        val content = args.optString("CodeContent", args.optString("content", ""))
        val overwrite = args.optBoolean("Overwrite", true)
        val f = resolveLocalFile(pathStr, workdir)
        return try {
            f.parentFile?.mkdirs()
            if (f.exists() && !overwrite) {
                return JSONObject().put("error", "Arquivo já existe e Overwrite=false: ${f.absolutePath}")
            }
            f.writeText(content)
            JSONObject()
                .put("status", "success")
                .put("path", f.absolutePath)
                .put("bytes_written", f.length())
        } catch (e: Exception) {
            JSONObject().put("error", e.message ?: "Erro ao gravar arquivo")
        }
    }

    private fun replaceFileTool(args: JSONObject, workdir: String): JSONObject {
        val pathStr = args.optString("TargetFile", "")
        val target = args.optString("TargetContent", "")
        val replacement = args.optString("ReplacementContent", "")
        val f = resolveLocalFile(pathStr, workdir)
        if (!f.exists() || !f.isFile) return JSONObject().put("error", "Arquivo não encontrado: ${f.absolutePath}")
        return try {
            val text = f.readText()
            if (!text.contains(target)) {
                return JSONObject().put("error", "TargetContent não encontrado em ${f.absolutePath}")
            }
            val updated = text.replaceFirst(target, replacement)
            f.writeText(updated)
            JSONObject().put("status", "success").put("path", f.absolutePath)
        } catch (e: Exception) {
            JSONObject().put("error", e.message ?: "Erro ao editar arquivo")
        }
    }

    private fun listDirTool(args: JSONObject, workdir: String): JSONObject {
        val pathStr = args.optString("DirectoryPath", args.optString("path", "."))
        val dir = resolveLocalFile(pathStr, workdir)
        if (!dir.exists()) return JSONObject().put("error", "Diretório não encontrado: ${dir.absolutePath}")
        if (!dir.isDirectory) return JSONObject().put("error", "Não é um diretório: ${dir.absolutePath}")
        val children = JSONArray()
        dir.listFiles()?.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))?.take(200)?.forEach { item ->
            children.put(
                JSONObject()
                    .put("name", item.name)
                    .put("type", if (item.isDirectory) "directory" else "file")
                    .put("size_bytes", if (item.isFile) item.length() else 0L)
            )
        }
        return JSONObject()
            .put("directory", dir.absolutePath)
            .put("count", children.length())
            .put("children", children)
    }

    private fun findByNameTool(args: JSONObject, workdir: String): JSONObject {
        val pathStr = args.optString("SearchDirectory", ".")
        val pattern = args.optString("Pattern", "").lowercase()
        val dir = resolveLocalFile(pathStr, workdir)
        if (!dir.exists() || !dir.isDirectory) return JSONObject().put("error", "Diretório inválido: ${dir.absolutePath}")
        val matches = JSONArray()
        dir.walkTopDown().maxDepth(6).forEach { f ->
            if (matches.length() >= 50) return@forEach
            if (f == dir) return@forEach
            if (pattern.isEmpty() || f.name.lowercase().contains(pattern.replace("*", ""))) {
                matches.put(
                    JSONObject()
                        .put("path", f.relativeToOrSelf(dir).path)
                        .put("type", if (f.isDirectory) "directory" else "file")
                        .put("size_bytes", if (f.isFile) f.length() else 0L)
                )
            }
        }
        return JSONObject().put("search_directory", dir.absolutePath).put("matches", matches)
    }

    private fun grepSearchTool(args: JSONObject, workdir: String): JSONObject {
        val pathStr = args.optString("SearchPath", ".")
        val query = args.optString("Query", "")
        val target = resolveLocalFile(pathStr, workdir)
        if (query.isEmpty() || !target.exists()) return JSONObject().put("error", "Parâmetros inválidos para grep_search")
        val matches = JSONArray()
        val files = if (target.isFile) sequenceOf(target) else target.walkTopDown().maxDepth(5).filter { it.isFile && it.length() < 512_000 }
        for (f in files) {
            if (matches.length() >= 50) break
            try {
                f.readLines().forEachIndexed { idx, line ->
                    if (matches.length() < 50 && line.contains(query, ignoreCase = true)) {
                        matches.put(
                            JSONObject()
                                .put("file", f.absolutePath)
                                .put("line", idx + 1)
                                .put("content", line.take(240))
                        )
                    }
                }
            } catch (_: Exception) {
            }
        }
        return JSONObject().put("query", query).put("matches", matches)
    }

    private fun webSearchTool(args: JSONObject): JSONObject {
        val qArr = args.optJSONArray("queries") ?: JSONArray()
        val results = JSONArray()
        for (i in 0 until qArr.length().coerceAtMost(3)) {
            val q = qArr.optString(i, "").trim()
            if (q.isEmpty()) continue
            try {
                val url = "https://html.duckduckgo.com/html/?q=${URLEncoder.encode(q, "UTF-8")}"
                val req = Request.Builder().url(url).header("User-Agent", "Mozilla/5.0").build()
                httpClient.newCall(req).execute().use { resp ->
                    val html = resp.body?.string().orEmpty()
                    val text = html.replace(Regex("<[^>]+>"), " ").replace(Regex("\\s+"), " ").take(4000)
                    results.put(JSONObject().put("query", q).put("snippet", text))
                }
            } catch (e: Exception) {
                results.put(JSONObject().put("query", q).put("error", e.message))
            }
        }
        return JSONObject().put("results", results)
    }

    private fun browseUrlTool(args: JSONObject): JSONObject {
        val uArr = args.optJSONArray("urls") ?: JSONArray()
        val pages = JSONArray()
        for (i in 0 until uArr.length().coerceAtMost(3)) {
            val u = uArr.optString(i, "").trim()
            if (u.isEmpty()) continue
            try {
                val req = Request.Builder().url(u).header("User-Agent", "Mozilla/5.0").build()
                httpClient.newCall(req).execute().use { resp ->
                    val html = resp.body?.string().orEmpty()
                    val text = html.replace(Regex("<(script|style)[^>]*>[\\s\\S]*?</\\1>", RegexOption.IGNORE_CASE), " ")
                        .replace(Regex("<[^>]+>"), " ")
                        .replace(Regex("\\s+"), " ")
                        .take(8000)
                    pages.put(JSONObject().put("url", u).put("content", text))
                }
            } catch (e: Exception) {
                pages.put(JSONObject().put("url", u).put("error", e.message))
            }
        }
        return JSONObject().put("pages", pages)
    }

    suspend fun executeToolCall(name: String, args: JSONObject, workdir: String): JSONObject = withContext(Dispatchers.IO) {
        when (name) {
            "execute_bash" -> executeBashTool(args, workdir)
            "view_file" -> viewFileTool(args, workdir)
            "write_to_file" -> writeFileTool(args, workdir)
            "replace_file_content" -> replaceFileTool(args, workdir)
            "list_dir" -> listDirTool(args, workdir)
            "find_by_name" -> findByNameTool(args, workdir)
            "grep_search" -> grepSearchTool(args, workdir)
            "web_search" -> webSearchTool(args)
            "browse_url" -> browseUrlTool(args)
            else -> JSONObject().put("error", "Ferramenta desconhecida: $name")
        }
    }

    private val localToolCallRegex = Regex(
        "<local_tool_call>\\s*([\\s\\S]*?)\\s*</local_tool_call>",
        RegexOption.IGNORE_CASE
    )

    private fun extractLocalToolCalls(text: String): Pair<List<Pair<String, JSONObject>>, String> {
        if (!text.contains("<local_tool_call>", ignoreCase = true)) {
            return emptyList<Pair<String, JSONObject>>() to text.trim()
        }
        val calls = mutableListOf<Pair<String, JSONObject>>()
        for (match in localToolCallRegex.findAll(text)) {
            var inner = match.groupValues[1].trim()
            if (inner.startsWith("```")) {
                inner = inner.replace(Regex("^```(?:json)?\\s*", RegexOption.IGNORE_CASE), "")
                    .replace(Regex("\\s*```$"), "")
            }
            try {
                val obj = JSONObject(inner)
                val tName = obj.optString("name", obj.optString("tool", "")).trim()
                val tArgs = obj.optJSONObject("arguments") ?: obj.optJSONObject("args") ?: JSONObject()
                if (tName.isNotEmpty()) {
                    calls.add(tName to tArgs)
                }
            } catch (_: Exception) {
            }
        }
        val cleaned = localToolCallRegex.replace(text, "").trim()
        return calls to cleaned
    }

    private suspend fun executeAntigravityAgentHop(
        hopInput: String,
        modelName: String,
        previousInteractionId: String?,
        apiKey: String
    ): JSONObject {
        val underlyingModel = "gemini-4-argon"
        val payload = JSONObject()
            .put("agent", "antigravity-preview-09-2026")
            .put("environment", "remote")
            .put(
                "agent_config",
                JSONObject()
                    .put("type", "antigravity")
                    .put("model", underlyingModel)
            )
            .put("input", hopInput)
            .put("background", true)
            .put("store", true)

        if (!previousInteractionId.isNullOrBlank() && previousInteractionId.startsWith("v1_")) {
            payload.put("previous_interaction_id", previousInteractionId)
        }

        val postUrl = "$INTERACTIONS_BASE_URL?key=${URLEncoder.encode(apiKey.trim(), "UTF-8")}"
        val postReq = Request.Builder()
            .url(postUrl)
            .header("x-goog-api-key", apiKey.trim())
            .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull()))
            .build()

        var data: JSONObject
        httpClient.newCall(postReq).execute().use { resp ->
            val bodyStr = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw IllegalStateException("Erro na Interactions API ($underlyingModel, HTTP ${resp.code}): $bodyStr")
            }
            data = JSONObject(bodyStr)
        }

        val interactionId = data.optString("id", "")
        var status = data.optString("status", "")
        var polls = 0
        while (status == "in_progress" && interactionId.isNotEmpty() && polls < 120) {
            delay(1500)
            polls++
            val pollUrl = "$INTERACTIONS_BASE_URL/$interactionId?key=${URLEncoder.encode(apiKey.trim(), "UTF-8")}"
            val pollReq = Request.Builder()
                .url(pollUrl)
                .header("x-goog-api-key", apiKey.trim())
                .get()
                .build()
            httpClient.newCall(pollReq).execute().use { pResp ->
                if (pResp.isSuccessful) {
                    val pBody = pResp.body?.string().orEmpty()
                    if (pBody.isNotBlank()) {
                        data = JSONObject(pBody)
                        status = data.optString("status", "")
                    }
                } else {
                    status = "completed"
                }
            }
        }
        return data
    }

    suspend fun runLocalAndroidTurn(
        prompt: String,
        workdir: String,
        modelName: String,
        systemInstruction: String,
        historyTurns: List<TurnModel>,
        apiKey: String
    ): JSONObject = withContext(Dispatchers.IO) {
        val localDir = File(workdir).apply { if (!exists()) mkdirs() }.absolutePath
        val localTurnId = "local_${UUID.randomUUID().toString().replace("-", "").take(16)}"
        val sysInst = buildSystemInstruction(localDir, systemInstruction)
        val underlyingModel = "gemini-4-argon"

        val historySnippets = mutableListOf<String>()
        historyTurns.takeLast(6).filter { it.status in listOf("completed", "incomplete") }.forEach { t ->
            val sb = StringBuilder()
            if (t.userInputText.isNotBlank()) {
                sb.appendLine("Usuário: ${t.userInputText.take(600)}")
            }
            if (t.outputText.isNotBlank()) {
                sb.appendLine("Agente Local: ${t.outputText.take(900)}")
            }
            if (sb.isNotEmpty()) {
                historySnippets.add(sb.toString().trim())
            }
        }

        val historyBlock = if (historySnippets.isNotEmpty()) {
            "\n\n[HISTÓRICO DE TURNOS NA SESSÃO LOCAL]:\n" + historySnippets.joinToString("\n---\n")
        } else {
            ""
        }

        var currentHopInput = "$sysInst$historyBlock\n\n[PEDIDO ATUAL DO USUÁRIO NO DISPOSITIVO LOCAL]:\n$prompt"
        var currentPrevId: String? = null

        val stepsRecorded = JSONArray()
        stepsRecorded.put(
            JSONObject()
                .put("type", "user_input")
                .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", prompt)))
        )

        var finalOutputText = ""
        var totalIn = 0
        var totalOut = 0
        var totalTok = 0

        for (hop in 0 until 10) {
            val hopData = executeAntigravityAgentHop(
                hopInput = currentHopInput,
                modelName = underlyingModel,
                previousInteractionId = currentPrevId,
                apiKey = apiKey
            )

            val hopId = hopData.optString("id", "")
            if (hopId.startsWith("v1_")) {
                currentPrevId = hopId
            }

            val usageObj = hopData.optJSONObject("usage")
            if (usageObj != null) {
                totalIn += usageObj.optInt("total_input_tokens", usageObj.optInt("input_tokens", 0))
                totalOut += usageObj.optInt("total_output_tokens", usageObj.optInt("output_tokens", 0))
                totalTok += usageObj.optInt("total_tokens", 0)
            }

            val hopSteps = hopData.optJSONArray("steps") ?: JSONArray()
            val hopTexts = mutableListOf<String>()
            for (i in 0 until hopSteps.length()) {
                val s = hopSteps.optJSONObject(i) ?: continue
                when (s.optString("type")) {
                    "thought" -> stepsRecorded.put(s)
                    "model_output" -> {
                        val contentArr = s.optJSONArray("content") ?: JSONArray()
                        for (j in 0 until contentArr.length()) {
                            val c = contentArr.optJSONObject(j) ?: continue
                            if (c.optString("type") == "text") {
                                val t = c.optString("text", "").trim()
                                if (t.isNotEmpty()) hopTexts.add(t)
                            }
                        }
                    }
                }
            }

            var rawHopOutput = hopTexts.lastOrNull() ?: hopData.optString("output_text", "").trim()
            if (rawHopOutput.isEmpty()) {
                val outputsArr = hopData.optJSONArray("outputs") ?: JSONArray()
                val outTxts = mutableListOf<String>()
                for (i in 0 until outputsArr.length()) {
                    val o = outputsArr.optJSONObject(i) ?: continue
                    if (o.optString("type") == "text") {
                        val t = o.optString("text", "").trim()
                        if (t.isNotEmpty()) outTxts.add(t)
                    }
                }
                rawHopOutput = outTxts.joinToString("\n\n").trim()
            }

            val (localCalls, cleanedOutput) = extractLocalToolCalls(rawHopOutput)
            if (localCalls.isEmpty()) {
                finalOutputText = cleanedOutput.ifBlank { rawHopOutput }
                if (finalOutputText.isNotEmpty()) {
                    stepsRecorded.put(
                        JSONObject()
                            .put("type", "model_output")
                            .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", finalOutputText)))
                    )
                }
                break
            }

            if (cleanedOutput.isNotEmpty()) {
                stepsRecorded.put(
                    JSONObject()
                        .put("type", "thought")
                        .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", cleanedOutput)))
                )
            }

            val feedbackBlocks = mutableListOf<String>()
            for ((fcName, fcArgs) in localCalls) {
                stepsRecorded.put(
                    JSONObject()
                        .put("type", "function_call")
                        .put("name", fcName)
                        .put("arguments", fcArgs)
                )
                val toolRes = executeToolCall(fcName, fcArgs, localDir)
                stepsRecorded.put(
                    JSONObject()
                        .put("type", "function_result")
                        .put("name", fcName)
                        .put("result", toolRes)
                )
                feedbackBlocks.add(
                    "<local_tool_result name=\"$fcName\">\n${toolRes.toString().take(20000)}\n</local_tool_result>"
                )
            }

            currentHopInput = "[RESULTADO REAL DA EXECUÇÃO NO DISPOSITIVO ANDROID LOCAL DO USUÁRIO]:\n" +
                feedbackBlocks.joinToString("\n\n") +
                "\n\n[INSTRUÇÃO]: Se precisar executar mais alguma ferramenta local, emita apenas `<local_tool_call>{...}</local_tool_call>`. Caso contrário, apresente a resposta final completa com base nos resultados acima (sem chamar ferramentas internas `default_api:*`)."
        }

        JSONObject()
            .put("id", currentPrevId ?: localTurnId)
            .put("status", "completed")
            .put("environment_id", "local:$localDir")
            .put("steps", stepsRecorded)
            .put("output_text", finalOutputText)
            .put(
                "outputs",
                JSONArray().put(JSONObject().put("type", "text").put("text", finalOutputText))
            )
            .put(
                "usage",
                JSONObject()
                    .put("input_tokens", totalIn)
                    .put("output_tokens", totalOut)
                    .put("total_tokens", totalTok)
            )
    }
}
