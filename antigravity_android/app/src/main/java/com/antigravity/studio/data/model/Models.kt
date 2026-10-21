package com.antigravity.studio.data.model

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

data class SourceItem(
    val type: String = "repository", // "repository", "gcs", "inline"
    val source: String = "",
    val target: String = "/workspace",
    val content: String = ""
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("type", type)
        if (source.isNotBlank()) put("source", source.trim())
        if (target.isNotBlank()) put("target", target.trim())
        if (content.isNotEmpty()) put("content", content)
    }

    companion object {
        fun fromJson(obj: JSONObject): SourceItem = SourceItem(
            type = obj.optString("type", "repository"),
            source = obj.optString("source", ""),
            target = obj.optString("target", "/workspace"),
            content = obj.optString("content", "")
        )
    }
}

data class UploadedFileRecord(
    val label: String = "",
    val summary: String = "",
    val targetPath: String = "/workspace",
    val sizeBytes: Long = 0L,
    val mimeType: String = "application/octet-stream",
    val extract: Boolean = false,
    val overwrite: Boolean = true,
    val resumable: Boolean = false,
    val uploadMode: String = "direct_put",
    val fileCount: Int = 1,
    val environmentId: String = "",
    val uploadedAt: String = nowIso()
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("label", label)
        put("summary", summary.ifBlank { label })
        put("target_path", targetPath)
        put("size_bytes", sizeBytes)
        put("mime_type", mimeType)
        put("extract", extract)
        put("overwrite", overwrite)
        put("resumable", resumable)
        put("upload_mode", uploadMode)
        put("file_count", fileCount)
        put("environment_id", environmentId)
        put("uploaded_at", uploadedAt)
    }

    companion object {
        fun fromJson(obj: JSONObject): UploadedFileRecord = UploadedFileRecord(
            label = obj.optString("label", obj.optString("summary", "arquivo")),
            summary = obj.optString("summary", obj.optString("label", "arquivo")),
            targetPath = obj.optString("target_path", "/workspace"),
            sizeBytes = obj.optLong("size_bytes", 0L),
            mimeType = obj.optString("mime_type", "application/octet-stream"),
            extract = obj.optBoolean("extract", false),
            overwrite = obj.optBoolean("overwrite", true),
            resumable = obj.optBoolean("resumable", false),
            uploadMode = obj.optString("upload_mode", "direct_put"),
            fileCount = obj.optInt("file_count", 1),
            environmentId = obj.optString("environment_id", ""),
            uploadedAt = obj.optString("uploaded_at", "")
        )
    }
}

data class StepItem(
    val type: String = "", // "user_input", "thought", "function_call", "function_response", "model_output", etc.
    val name: String = "",
    val textContent: String = "",
    val argumentsJson: String = "",
    val rawJson: String = "{}"
)

data class UsageInfo(
    val inputTokens: Int = 0,
    val outputTokens: Int = 0,
    val totalTokens: Int = 0,
    val rawJson: String = "{}"
) {
    companion object {
        fun fromJsonString(jsonStr: String): UsageInfo {
            if (jsonStr.isBlank()) return UsageInfo()
            return try {
                val obj = JSONObject(jsonStr)
                val inTok = obj.optInt("input_tokens", obj.optInt("prompt_token_count", 0))
                val outTok = obj.optInt("output_tokens", obj.optInt("candidates_token_count", 0))
                val totTok = obj.optInt("total_tokens", obj.optInt("total_token_count", inTok + outTok))
                UsageInfo(
                    inputTokens = inTok,
                    outputTokens = outTok,
                    totalTokens = totTok,
                    rawJson = jsonStr
                )
            } catch (_: Exception) {
                UsageInfo(rawJson = jsonStr)
            }
        }
    }
}

data class TurnModel(
    val interactionId: String,
    val sessionId: String,
    val turnIndex: Int,
    val previousInteractionId: String = "",
    val environmentId: String = "",
    val userInputText: String = "",
    val outputText: String = "",
    val status: String = "completed",
    val steps: List<StepItem> = emptyList(),
    val stepsJson: String = "[]",
    val usage: UsageInfo = UsageInfo(),
    val usageJson: String = "{}",
    val rawJson: String = "{}",
    val createdAt: String = nowIso()
)

data class SessionModel(
    val id: String,
    val title: String = "Nova Conversa Antigravity",
    val mode: String = "agent", // "agent" or "model"
    val agentName: String = "antigravity-preview-09-2026",
    val modelName: String = "gemini-3.8-flash",
    val environmentId: String = "",
    val lastInteractionId: String = "",
    val maxTotalTokens: Int = 0,
    val systemInstruction: String = "",
    val sources: List<SourceItem> = emptyList(),
    val uploadedFiles: List<UploadedFileRecord> = emptyList(),
    val turns: List<TurnModel> = emptyList(),
    val turnCount: Int = 0,
    val createdAt: String = nowIso(),
    val updatedAt: String = nowIso()
)

data class EnvFileEntry(
    val name: String,
    val path: String,
    val sizeBytes: Long = 0L,
    val mimeType: String = "",
    val type: String = "file", // "file" or "directory"
    val modified: String = ""
) {
    val isDirectory: Boolean
        get() = type.equals("directory", ignoreCase = true)
}

fun nowIso(): String =
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'")
        .withZone(ZoneOffset.UTC)
        .format(Instant.now())

fun parseSourcesJson(jsonStr: String?): List<SourceItem> {
    if (jsonStr.isNullOrBlank()) return emptyList()
    return try {
        val arr = JSONArray(jsonStr)
        buildList {
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                add(SourceItem.fromJson(obj))
            }
        }
    } catch (_: Exception) {
        emptyList()
    }
}

fun encodeSourcesJson(sources: List<SourceItem>): String {
    val arr = JSONArray()
    sources.forEach { arr.put(it.toJson()) }
    return arr.toString()
}

fun parseUploadedFilesJson(jsonStr: String?): List<UploadedFileRecord> {
    if (jsonStr.isNullOrBlank()) return emptyList()
    return try {
        val arr = JSONArray(jsonStr)
        buildList {
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                add(UploadedFileRecord.fromJson(obj))
            }
        }
    } catch (_: Exception) {
        emptyList()
    }
}

fun encodeUploadedFilesJson(files: List<UploadedFileRecord>): String {
    val arr = JSONArray()
    files.forEach { arr.put(it.toJson()) }
    return arr.toString()
}

fun parseStepsJson(jsonStr: String?): List<StepItem> {
    if (jsonStr.isNullOrBlank()) return emptyList()
    return try {
        val arr = JSONArray(jsonStr)
        buildList {
            for (i in 0 until arr.length()) {
                val stepObj = arr.optJSONObject(i) ?: continue
                val stype = stepObj.optString("type", "")
                val name = stepObj.optString("name", "")
                val contents = stepObj.optJSONArray("content")
                val texts = mutableListOf<String>()
                if (contents != null) {
                    for (j in 0 until contents.length()) {
                        val c = contents.optJSONObject(j) ?: continue
                        val ctype = c.optString("type", "")
                        if (ctype == "text") {
                            val t = c.optString("text", "")
                            if (t.isNotBlank()) texts.add(t)
                        } else if (c.has("function_call") || ctype == "function_call") {
                            val fc = c.optJSONObject("function_call") ?: c
                            texts.add("${fc.optString("name", "tool")}: ${fc.opt("args") ?: fc.opt("arguments") ?: ""}")
                        }
                    }
                }
                val argsObj = stepObj.opt("arguments") ?: stepObj.opt("args")
                val argsStr = when (argsObj) {
                    is JSONObject -> argsObj.toString(2)
                    is JSONArray -> argsObj.toString(2)
                    null -> if (stype in listOf("function_call", "code_execution_call", "google_search_call")) stepObj.toString(2) else ""
                    else -> argsObj.toString()
                }
                add(
                    StepItem(
                        type = stype,
                        name = name.ifBlank { stype },
                        textContent = texts.joinToString("\n").trim(),
                        argumentsJson = argsStr,
                        rawJson = stepObj.toString()
                    )
                )
            }
        }
    } catch (_: Exception) {
        emptyList()
    }
}
