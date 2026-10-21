package com.antigravity.studio

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.viewmodel.compose.viewModel
import com.antigravity.studio.ui.StudioViewModel
import com.antigravity.studio.ui.screens.MainScreen
import com.antigravity.studio.ui.theme.AntigravityStudioTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = application as AntigravityApp
        setContent {
            AntigravityStudioTheme {
                val studioViewModel: StudioViewModel = viewModel(
                    factory = StudioViewModel.Factory(app.repository)
                )
                MainScreen(viewModel = studioViewModel)
            }
        }
    }
}
