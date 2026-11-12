package com.antigravity.studio.data.local

import android.content.Context
import android.database.sqlite.SQLiteBlobTooBigException
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Transaction
import androidx.room.Update
import com.antigravity.studio.data.model.SessionModel
import com.antigravity.studio.data.model.SourceItem
import com.antigravity.studio.data.model.TurnModel
import com.antigravity.studio.data.model.UploadedFileRecord
import com.antigravity.studio.data.model.UsageInfo
import com.antigravity.studio.data.model.encodeSourcesJson
import com.antigravity.studio.data.model.encodeUploadedFilesJson
import com.antigravity.studio.data.model.nowIso
import com.antigravity.studio.data.model.parseSourcesJson
import com.antigravity.studio.data.model.parseStepsJson
import com.antigravity.studio.data.model.parseUploadedFilesJson
import com.antigravity.studio.data.remote.InteractionsClient
import java.util.UUID

@Entity(tableName = "sessions")
data class SessionEntity(
    @PrimaryKey val id: String,
    val title: String,
    @ColumnInfo(name = "title_locked_by_user", defaultValue = "0") val titleLockedByUser: Int = 0,
    val mode: String = "agent",
    @ColumnInfo(name = "agent_name") val agentName: String = "antigravity-preview-09-2026",
    @ColumnInfo(name = "model_name") val modelName: String = "gemini-4-argon",
    @ColumnInfo(name = "environment_id") val environmentId: String = "",
    @ColumnInfo(name = "last_interaction_id") val lastInteractionId: String = "",
    @ColumnInfo(name = "max_total_tokens") val maxTotalTokens: Int = 0,
    @ColumnInfo(name = "system_instruction") val systemInstruction: String = "",
    @ColumnInfo(name = "sources_json") val sourcesJson: String = "[]",
    @ColumnInfo(name = "uploaded_files_json") val uploadedFilesJson: String = "[]",
    @ColumnInfo(name = "created_at") val createdAt: String,
    @ColumnInfo(name = "updated_at") val updatedAt: String
)

@Entity(
    tableName = "turns",
    primaryKeys = ["session_id", "interaction_id"],
    foreignKeys = [
        ForeignKey(
            entity = SessionEntity::class,
            parentColumns = ["id"],
            childColumns = ["session_id"],
            onDelete = ForeignKey.CASCADE
        )
    ],
    indices = [Index("session_id")]
)
data class TurnEntity(
    @ColumnInfo(name = "interaction_id") val interactionId: String,
    @ColumnInfo(name = "session_id") val sessionId: String,
    @ColumnInfo(name = "turn_index") val turnIndex: Int,
    @ColumnInfo(name = "previous_interaction_id") val previousInteractionId: String = "",
    @ColumnInfo(name = "environment_id") val environmentId: String = "",
    @ColumnInfo(name = "user_input_text") val userInputText: String = "",
    @ColumnInfo(name = "output_text") val outputText: String = "",
    val status: String = "completed",
    @ColumnInfo(name = "steps_json") val stepsJson: String = "[]",
    @ColumnInfo(name = "usage_json") val usageJson: String = "{}",
    @ColumnInfo(name = "raw_json") val rawJson: String = "{}",
    @ColumnInfo(name = "created_at") val createdAt: String
)

@Entity(tableName = "settings")
data class SettingEntity(
    @PrimaryKey val key: String,
    val value: String
)

data class TurnMetadataProjection(
    @ColumnInfo(name = "interaction_id") val interactionId: String,
    @ColumnInfo(name = "session_id") val sessionId: String,
    @ColumnInfo(name = "turn_index") val turnIndex: Int,
    @ColumnInfo(name = "previous_interaction_id") val previousInteractionId: String,
    @ColumnInfo(name = "environment_id") val environmentId: String,
    val status: String,
    @ColumnInfo(name = "usage_json") val usageJson: String,
    @ColumnInfo(name = "created_at") val createdAt: String,
    @ColumnInfo(name = "user_input_len") val userInputLen: Int,
    @ColumnInfo(name = "output_len") val outputLen: Int,
    @ColumnInfo(name = "steps_len") val stepsLen: Int,
    @ColumnInfo(name = "raw_len") val rawLen: Int
)

@Dao
interface StudioDao {
    @Query("SELECT value FROM settings WHERE `key` = :key LIMIT 1")
    suspend fun getSetting(key: String): String?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun setSetting(setting: SettingEntity)

    @Query("SELECT * FROM sessions ORDER BY updated_at DESC")
    suspend fun listSessionsRaw(): List<SessionEntity>

    @Query("SELECT COUNT(*) FROM turns WHERE session_id = :sessionId")
    suspend fun countTurnsForSession(sessionId: String): Int

    @Query("SELECT MAX(created_at) FROM turns WHERE session_id = :sessionId")
    suspend fun getLastPromptAtForSession(sessionId: String): String?

    @Query("SELECT * FROM sessions WHERE id = :sessionId LIMIT 1")
    suspend fun getSessionRaw(sessionId: String): SessionEntity?

    @Query("SELECT * FROM turns WHERE session_id = :sessionId ORDER BY turn_index ASC")
    suspend fun getTurnsForSession(sessionId: String): List<TurnEntity>

    @Query(
        """
        SELECT interaction_id, session_id, turn_index, previous_interaction_id,
               environment_id, status, usage_json, created_at,
               LENGTH(user_input_text) AS user_input_len,
               LENGTH(output_text) AS output_len,
               LENGTH(steps_json) AS steps_len,
               LENGTH(raw_json) AS raw_len
        FROM turns
        WHERE session_id = :sessionId
        ORDER BY turn_index ASC
        """
    )
    suspend fun getTurnMetadataForSession(sessionId: String): List<TurnMetadataProjection>

    @Query(
        """
        SELECT interaction_id, session_id, turn_index, previous_interaction_id,
               environment_id, status, usage_json, created_at,
               LENGTH(user_input_text) AS user_input_len,
               LENGTH(output_text) AS output_len,
               LENGTH(steps_json) AS steps_len,
               LENGTH(raw_json) AS raw_len
        FROM turns
        WHERE session_id = :sessionId AND interaction_id = :interactionId
        LIMIT 1
        """
    )
    suspend fun getTurnMetadataByKey(sessionId: String, interactionId: String): TurnMetadataProjection?

    @Query("SELECT SUBSTR(user_input_text, :start, :length) FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId LIMIT 1")
    suspend fun getTurnUserInputChunk(sessionId: String, interactionId: String, start: Int, length: Int): String?

    @Query("SELECT SUBSTR(output_text, :start, :length) FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId LIMIT 1")
    suspend fun getTurnOutputChunk(sessionId: String, interactionId: String, start: Int, length: Int): String?

    @Query("SELECT SUBSTR(steps_json, :start, :length) FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId LIMIT 1")
    suspend fun getTurnStepsChunk(sessionId: String, interactionId: String, start: Int, length: Int): String?

    @Query("SELECT SUBSTR(raw_json, :start, :length) FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId LIMIT 1")
    suspend fun getTurnRawChunk(sessionId: String, interactionId: String, start: Int, length: Int): String?

    @Query("SELECT created_at FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId LIMIT 1")
    suspend fun getTurnCreatedAt(sessionId: String, interactionId: String): String?

    @Query("SELECT LENGTH(user_input_text) FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId LIMIT 1")
    suspend fun getTurnUserInputLength(sessionId: String, interactionId: String): Int?

    @Query(
        """
        SELECT session_id FROM turns
        WHERE interaction_id = :interactionId
        ORDER BY CASE WHEN LENGTH(user_input_text) > 0 THEN 0 ELSE 1 END, created_at DESC
        LIMIT 1
        """
    )
    suspend fun findAnySessionIdForInteractionId(interactionId: String): String?

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertSession(session: SessionEntity)

    @Update
    suspend fun updateSessionEntity(session: SessionEntity)

    @Query("DELETE FROM turns WHERE session_id = :sessionId")
    suspend fun deleteTurnsForSession(sessionId: String)

    @Query("DELETE FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId")
    suspend fun deleteTurnById(sessionId: String, interactionId: String)

    @Query("DELETE FROM sessions WHERE id = :sessionId")
    suspend fun deleteSessionById(sessionId: String)

    @Query("SELECT * FROM turns WHERE session_id = :sessionId AND interaction_id = :interactionId LIMIT 1")
    suspend fun getTurnRaw(sessionId: String, interactionId: String): TurnEntity?

    @Query(
        """
        SELECT * FROM turns
        WHERE interaction_id = :interactionId
        ORDER BY CASE WHEN user_input_text != '' THEN 0 ELSE 1 END, created_at DESC
        LIMIT 1
        """
    )
    suspend fun findAnyTurnByInteractionId(interactionId: String): TurnEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertTurn(turn: TurnEntity)
}

@Database(
    entities = [SessionEntity::class, TurnEntity::class, SettingEntity::class],
    version = 2,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun studioDao(): StudioDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        private val MIGRATION_1_2 = object : androidx.room.migration.Migration(1, 2) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                try {
                    db.execSQL("ALTER TABLE sessions ADD COLUMN title_locked_by_user INTEGER NOT NULL DEFAULT 0")
                } catch (_: Exception) {
                }
            }
        }

        fun getInstance(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "antigravity_studio.db"
                )
                    .addMigrations(MIGRATION_1_2)
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { INSTANCE = it }
            }
        }
    }
}

class StudioRepository(private val dao: StudioDao) {

    suspend fun getApiKey(): String =
        (dao.getSetting("gemini_api_key") ?: "").trim()

    suspend fun setApiKey(apiKey: String) {
        dao.setSetting(SettingEntity(key = "gemini_api_key", value = apiKey.trim()))
    }

    suspend fun getSessionSortOrder(): String =
        (dao.getSetting("session_sort_order") ?: "last_interaction_desc").trim()

    suspend fun setSessionSortOrder(orderKey: String) {
        dao.setSetting(SettingEntity(key = "session_sort_order", value = orderKey.trim()))
    }

    suspend fun listSessions(): List<SessionModel> {
        val entities = dao.listSessionsRaw()
        return entities.map { entity ->
            val count = dao.countTurnsForSession(entity.id)
            val lastPrompt = dao.getLastPromptAtForSession(entity.id).orEmpty()
            entity.toDomain(turns = emptyList(), turnCount = count, lastPromptAt = lastPrompt)
        }
    }

    suspend fun getSession(sessionId: String): SessionModel? {
        val entity = dao.getSessionRaw(sessionId) ?: return null
        val turnEntities = getTurnsForSessionSafe(sessionId)
        val turns = turnEntities.map { it.toDomain() }
        val lastPrompt = turns.maxOfOrNull { it.createdAt }.orEmpty()
        return entity.toDomain(turns = turns, turnCount = turns.size, lastPromptAt = lastPrompt)
    }

    suspend fun findUploadedFilesForEnvironment(environmentId: String?): List<UploadedFileRecord> {
        val cleanEnv = InteractionsClient.cleanEnvironmentId(environmentId ?: "")
        if (cleanEnv.isBlank() || cleanEnv == "remote") return emptyList()
        val allSessions = listSessions()
        val merged = mutableListOf<UploadedFileRecord>()
        val seenKeys = mutableSetOf<String>()
        for (s in allSessions) {
            val sEnv = InteractionsClient.cleanEnvironmentId(s.environmentId)
            for (uf in s.uploadedFiles) {
                val ufEnv = InteractionsClient.cleanEnvironmentId(uf.environmentId.ifBlank { sEnv })
                if (ufEnv == cleanEnv) {
                    val key = "${uf.targetPath}|${uf.label}|${uf.uploadedAt}"
                    if (seenKeys.add(key)) {
                        merged.add(uf)
                    }
                }
            }
        }
        return merged
    }

    suspend fun createSession(
        title: String = "Nova Conversa Antigravity",
        titleLockedByUser: Boolean = false,
        mode: String = "agent",
        agentName: String = "antigravity-preview-09-2026",
        modelName: String = "gemini-4-argon",
        environmentId: String = "",
        lastInteractionId: String = "",
        maxTotalTokens: Int = 0,
        systemInstruction: String = "",
        sources: List<SourceItem> = emptyList(),
        uploadedFiles: List<UploadedFileRecord>? = null,
        customId: String? = null,
        customUpdatedAt: String? = null
    ): SessionModel {
        val sessionId = customId?.trim()?.takeIf { it.isNotEmpty() }
            ?: "sess_${UUID.randomUUID().toString().replace("-", "").take(12)}"
        val ts = nowIso()
        val effUpdated = customUpdatedAt?.trim()?.takeIf { it.isNotEmpty() } ?: ts
        val effectiveUploads = uploadedFiles ?: if (environmentId.isNotBlank()) {
            findUploadedFilesForEnvironment(environmentId)
        } else {
            emptyList()
        }
        val entity = SessionEntity(
            id = sessionId,
            title = title.ifBlank { "Nova Conversa Antigravity" },
            titleLockedByUser = if (titleLockedByUser) 1 else 0,
            mode = mode.ifBlank { "agent" },
            agentName = agentName.ifBlank { "antigravity-preview-09-2026" },
            modelName = modelName.ifBlank { "gemini-4-argon" },
            environmentId = environmentId.trim(),
            lastInteractionId = lastInteractionId.trim(),
            maxTotalTokens = maxTotalTokens,
            systemInstruction = systemInstruction,
            sourcesJson = encodeSourcesJson(sources),
            uploadedFilesJson = encodeUploadedFilesJson(effectiveUploads),
            createdAt = ts,
            updatedAt = effUpdated
        )
        dao.insertSession(entity)
        return getSession(sessionId)!!
    }

    suspend fun updateSession(
        sessionId: String,
        title: String? = null,
        titleLockedByUser: Boolean? = null,
        mode: String? = null,
        agentName: String? = null,
        modelName: String? = null,
        environmentId: String? = null,
        lastInteractionId: String? = null,
        maxTotalTokens: Int? = null,
        systemInstruction: String? = null,
        sources: List<SourceItem>? = null,
        uploadedFiles: List<UploadedFileRecord>? = null
    ): SessionModel? {
        val existing = dao.getSessionRaw(sessionId) ?: return null
        val currentUploads = parseUploadedFilesJson(existing.uploadedFilesJson)
        val inheritedUploads = if (
            uploadedFiles == null &&
            !environmentId.isNullOrBlank() &&
            currentUploads.isEmpty()
        ) {
            findUploadedFilesForEnvironment(environmentId)
        } else {
            null
        }

        val updated = existing.copy(
            title = title ?: existing.title,
            titleLockedByUser = titleLockedByUser?.let { if (it) 1 else 0 } ?: existing.titleLockedByUser,
            mode = mode ?: existing.mode,
            agentName = agentName ?: existing.agentName,
            modelName = modelName ?: existing.modelName,
            environmentId = environmentId ?: existing.environmentId,
            lastInteractionId = lastInteractionId ?: existing.lastInteractionId,
            maxTotalTokens = maxTotalTokens ?: existing.maxTotalTokens,
            systemInstruction = systemInstruction ?: existing.systemInstruction,
            sourcesJson = sources?.let { encodeSourcesJson(it) } ?: existing.sourcesJson,
            uploadedFilesJson = (uploadedFiles ?: inheritedUploads)?.let { encodeUploadedFilesJson(it) }
                ?: existing.uploadedFilesJson,
            updatedAt = nowIso()
        )
        dao.updateSessionEntity(updated)
        return getSession(sessionId)
    }

    @Transaction
    suspend fun deleteSession(sessionId: String) {
        dao.deleteTurnsForSession(sessionId)
        dao.deleteSessionById(sessionId)
    }

    suspend fun deleteTurn(sessionId: String, interactionId: String) {
        dao.deleteTurnById(sessionId, interactionId)
    }

    suspend fun findTurnByInteractionId(interactionId: String): TurnModel? {
        val clean = interactionId.trim()
        if (clean.isBlank()) return null
        return try {
            dao.findAnyTurnByInteractionId(clean)?.toDomain()
        } catch (e: Exception) {
            if (!isBlobTooBigException(e)) throw e
            val sid = dao.findAnySessionIdForInteractionId(clean) ?: return null
            val meta = dao.getTurnMetadataByKey(sid, clean) ?: return null
            loadTurnFromMetadata(meta).toDomain()
        }
    }

    suspend fun upsertTurn(
        sessionId: String,
        interactionId: String,
        turnIndex: Int,
        previousInteractionId: String,
        environmentId: String,
        userInputText: String,
        outputText: String,
        status: String,
        stepsJson: String,
        usageJson: String,
        rawJson: String,
        createdAt: String? = null
    ) {
        val existingCreatedAt = dao.getTurnCreatedAt(sessionId, interactionId)
        val finalUserText = when {
            userInputText.isNotBlank() -> userInputText
            else -> {
                val ownText = readTurnUserInputSafe(sessionId, interactionId)
                if (ownText.isNotBlank()) {
                    ownText
                } else {
                    val otherSessionId = dao.findAnySessionIdForInteractionId(interactionId)
                    if (!otherSessionId.isNullOrBlank()) {
                        readTurnUserInputSafe(otherSessionId, interactionId)
                    } else {
                        ""
                    }
                }
            }
        }
        val entity = TurnEntity(
            interactionId = interactionId,
            sessionId = sessionId,
            turnIndex = turnIndex,
            previousInteractionId = previousInteractionId,
            environmentId = environmentId,
            userInputText = finalUserText,
            outputText = outputText,
            status = status.ifBlank { "completed" },
            stepsJson = stepsJson.ifBlank { "[]" },
            usageJson = usageJson.ifBlank { "{}" },
            rawJson = compactRawJsonForStorage(rawJson),
            createdAt = createdAt ?: existingCreatedAt ?: nowIso()
        )
        dao.insertTurn(entity)
    }

    private suspend fun getTurnsForSessionSafe(sessionId: String): List<TurnEntity> {
        return try {
            dao.getTurnsForSession(sessionId)
        } catch (e: Exception) {
            if (!isBlobTooBigException(e)) throw e
            dao.getTurnMetadataForSession(sessionId).map { meta ->
                loadTurnFromMetadata(meta)
            }
        }
    }

    private suspend fun loadTurnFromMetadata(meta: TurnMetadataProjection): TurnEntity {
        val userInput = readChunkedText(meta.userInputLen) { start, len ->
            dao.getTurnUserInputChunk(meta.sessionId, meta.interactionId, start, len)
        }
        val output = readChunkedText(meta.outputLen) { start, len ->
            dao.getTurnOutputChunk(meta.sessionId, meta.interactionId, start, len)
        }
        val steps = readChunkedText(meta.stepsLen) { start, len ->
            dao.getTurnStepsChunk(meta.sessionId, meta.interactionId, start, len)
        }.ifBlank { "[]" }
        val raw = if (meta.rawLen in 1..MAX_STORED_RAW_JSON_CHARS) {
            readChunkedText(meta.rawLen) { start, len ->
                dao.getTurnRawChunk(meta.sessionId, meta.interactionId, start, len)
            }.ifBlank { "{}" }
        } else {
            "{}"
        }
        return TurnEntity(
            interactionId = meta.interactionId,
            sessionId = meta.sessionId,
            turnIndex = meta.turnIndex,
            previousInteractionId = meta.previousInteractionId,
            environmentId = meta.environmentId,
            userInputText = userInput,
            outputText = output,
            status = meta.status,
            stepsJson = steps,
            usageJson = meta.usageJson,
            rawJson = raw,
            createdAt = meta.createdAt
        )
    }

    private suspend fun readTurnUserInputSafe(sessionId: String, interactionId: String): String {
        val len = dao.getTurnUserInputLength(sessionId, interactionId) ?: 0
        if (len <= 0) return ""
        return readChunkedText(len) { start, chunkLen ->
            dao.getTurnUserInputChunk(sessionId, interactionId, start, chunkLen)
        }
    }

    private suspend fun readChunkedText(
        totalLength: Int,
        chunkSize: Int = CURSOR_TEXT_CHUNK_CHARS,
        fetchChunk: suspend (start: Int, length: Int) -> String?
    ): String {
        if (totalLength <= 0) return ""
        if (totalLength <= chunkSize) {
            return fetchChunk(1, totalLength) ?: ""
        }
        val sb = StringBuilder(totalLength)
        var offset = 1
        while (offset <= totalLength) {
            val part = fetchChunk(offset, chunkSize) ?: break
            if (part.isEmpty()) break
            sb.append(part)
            if (part.length < chunkSize) break
            offset += part.length
        }
        return sb.toString()
    }

    private fun compactRawJsonForStorage(rawJson: String): String {
        val trimmed = rawJson.ifBlank { "{}" }
        return if (trimmed.length <= MAX_STORED_RAW_JSON_CHARS) {
            trimmed
        } else {
            """{"truncated":true,"original_length":${trimmed.length}}"""
        }
    }

    private fun isBlobTooBigException(t: Throwable?): Boolean {
        var cur = t
        while (cur != null) {
            if (cur is SQLiteBlobTooBigException) return true
            val msg = cur.message.orEmpty()
            if (msg.contains("SQLiteBlobTooBigException", ignoreCase = true) ||
                msg.contains("Row too big to fit into CursorWindow", ignoreCase = true) ||
                msg.contains("CursorWindow", ignoreCase = true)
            ) {
                return true
            }
            cur = cur.cause
        }
        return false
    }

    companion object {
        private const val CURSOR_TEXT_CHUNK_CHARS = 250_000
        private const val MAX_STORED_RAW_JSON_CHARS = 200_000
    }

    suspend fun forkSession(
        sourceSessionId: String,
        upToInteractionId: String,
        customTitle: String? = null
    ): SessionModel? {
        val source = getSession(sourceSessionId) ?: return null
        val selectedTurns = mutableListOf<TurnModel>()
        var targetEnv = source.environmentId

        for (t in source.turns) {
            selectedTurns.add(t)
            if (t.environmentId.isNotBlank()) {
                targetEnv = t.environmentId
            }
            if (t.interactionId == upToInteractionId) {
                break
            }
        }

        val newTitle = customTitle?.takeIf { it.isNotBlank() }
            ?: "Fork: ${source.title} (Turno ${selectedTurns.size})"

        val forked = createSession(
            title = newTitle,
            mode = source.mode,
            agentName = source.agentName,
            modelName = source.modelName,
            environmentId = targetEnv,
            lastInteractionId = upToInteractionId,
            maxTotalTokens = source.maxTotalTokens,
            systemInstruction = source.systemInstruction,
            sources = source.sources,
            uploadedFiles = source.uploadedFiles
        )

        selectedTurns.forEachIndexed { idx, t ->
            upsertTurn(
                sessionId = forked.id,
                interactionId = t.interactionId,
                turnIndex = idx + 1,
                previousInteractionId = t.previousInteractionId,
                environmentId = t.environmentId,
                userInputText = t.userInputText,
                outputText = t.outputText,
                status = t.status,
                stepsJson = t.stepsJson,
                usageJson = t.usageJson,
                rawJson = t.rawJson,
                createdAt = t.createdAt
            )
        }

        return getSession(forked.id)
    }

    private fun SessionEntity.toDomain(
        turns: List<TurnModel>,
        turnCount: Int,
        lastPromptAt: String = ""
    ): SessionModel = SessionModel(
        id = id,
        title = title,
        titleLockedByUser = titleLockedByUser != 0,
        mode = mode,
        agentName = agentName,
        modelName = modelName,
        environmentId = environmentId,
        lastInteractionId = lastInteractionId,
        maxTotalTokens = maxTotalTokens,
        systemInstruction = systemInstruction,
        sources = parseSourcesJson(sourcesJson),
        uploadedFiles = parseUploadedFilesJson(uploadedFilesJson),
        turns = turns,
        turnCount = turnCount,
        lastPromptAt = lastPromptAt,
        createdAt = createdAt,
        updatedAt = updatedAt
    )

    private fun TurnEntity.toDomain(): TurnModel = TurnModel(
        interactionId = interactionId,
        sessionId = sessionId,
        turnIndex = turnIndex,
        previousInteractionId = previousInteractionId,
        environmentId = environmentId,
        userInputText = userInputText,
        outputText = outputText,
        status = status,
        steps = parseStepsJson(stepsJson),
        stepsJson = stepsJson,
        usage = UsageInfo.fromJsonString(usageJson),
        usageJson = usageJson,
        rawJson = rawJson,
        createdAt = createdAt
    )
}
