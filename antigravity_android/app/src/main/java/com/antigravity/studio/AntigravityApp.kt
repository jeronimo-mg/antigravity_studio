package com.antigravity.studio

import android.app.Application
import com.antigravity.studio.data.local.AppDatabase
import com.antigravity.studio.data.local.StudioRepository

class AntigravityApp : Application() {
    val database: AppDatabase by lazy { AppDatabase.getInstance(this) }
    val repository: StudioRepository by lazy { StudioRepository(database.studioDao()) }
}
