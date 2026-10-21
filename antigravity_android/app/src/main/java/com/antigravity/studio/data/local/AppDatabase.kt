package com.antigravity.studio.data.local

import android.content.Context
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
    val mode: String = "agent",
    @ColumnInfo(name = "agent_name") val agentName: String = "antigravity-preview-09-2026",
    @ColumnInfo(name = "model_name") val modelName: String = "gemini-3.8-flash",
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

    @Query("SELECT * FROM sessions WHERE id = :sessionId LIMIT 1")
    suspend fun getSessionRaw(sessionId: String): SessionEntity?

    @Query("SELECT * FROM turns WHERE session_id = :sessionId ORDER BY turn_index ASC")
    suspend fun getTurnsForSession(sessionId: String): List<TurnEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSession(session: SessionEntity)

    @Query("DELETE FROM turns WHERE session_id = :sessionId")
    suspend fun deleteTurnsForSession(sessionId: String)

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
    version = 1,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun studioDao(): StudioDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getInstance(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "antigravity_studio.db"
                ).fallbackToDestructiveMigration().build().also { INSTANCE = it }
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

    suspend fun listSessions(): List<SessionModel> {
        val entities = dao.listSessionsRaw()
        return entities.map { entity ->
            val count = dao.countTurnsForSession(entity.id)
            entity.toDomain(turns = emptyList(), turnCount = count)
        }
    }

    suspend fun getSession(sessionId: String): SessionModel? {
        val entity = dao.getSessionRaw(sessionId) ?: return null
        val turnEntities = dao.getTurnsForSession(sessionId)
        val turns = turnEntities.map { it.toDomain() }
        return entity.toDomain(turns = turns, turnCount = turns.size)
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
        mode: String = "agent",
        agentName: String = "antigravity-preview-09-2026",
        modelName: String = "gemini-3.8-flash",
        environmentId: String = "",
        lastInteractionId: String = "",
        maxTotalTokens: Int = 0,
        systemInstruction: String = "",
        sources: List<SourceItem> = emptyList(),
        uploadedFiles: List<UploadedFileRecord>? = null
    ): SessionModel {
        val sessionId = "sess_${UUID.randomUUID().toString().replace("-", "").take(12)}"
        val ts = nowIso()
        val effectiveUploads = uploadedFiles ?: if (environmentId.isNotBlank()) {
            findUploadedFilesForEnvironment(environmentId)
        } else {
            emptyList()
        }
        val entity = SessionEntity(
            id = sessionId,
            title = title.ifBlank { "Nova Conversa Antigravity" },
            mode = mode.ifBlank { "agent" },
            agentName = agentName.ifBlank { "antigravity-preview-09-2026" },
            modelName = modelName.ifBlank { "gemini-3.8-flash" },
            environmentId = environmentId.trim(),
            lastInteractionId = lastInteractionId.trim(),
            maxTotalTokens = maxTotalTokens,
            systemInstruction = systemInstruction,
            sourcesJson = encodeSourcesJson(sources),
            uploadedFilesJson = encodeUploadedFilesJson(effectiveUploads),
            createdAt = ts,
            updatedAt = ts
        )
        dao.insertSession(entity)
        return getSession(sessionId)!!
    }

    suspend fun updateSession(
        sessionId: String,
        title: String? = null,
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
        dao.insertSession(updated)
        return getSession(sessionId)
    }

    @Transaction
    suspend fun deleteSession(sessionId: String) {
        dao.deleteTurnsForSession(sessionId)
        dao.deleteSessionById(sessionId)
    }

    suspend fun findTurnByInteractionId(interactionId: String): TurnModel? {
        val clean = interactionId.trim()
        if (clean.isBlank()) return null
        return dao.findAnyTurnByInteractionId(clean)?.toDomain()
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
        val existing = dao.getTurnRaw(sessionId, interactionId)
        val finalUserText = when {
            userInputText.isNotBlank() -> userInputText
            !existing?.userInputText.isNullOrBlank() -> existing!!.userInputText
            else -> dao.findAnyTurnByInteractionId(interactionId)?.userInputText ?: ""
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
            rawJson = rawJson.ifBlank { "{}" },
            createdAt = createdAt ?: existing?.createdAt ?: nowIso()
        )
        dao.insertTurn(entity)
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
        turnCount: Int
    ): SessionModel = SessionModel(
        id = id,
        title = title,
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
