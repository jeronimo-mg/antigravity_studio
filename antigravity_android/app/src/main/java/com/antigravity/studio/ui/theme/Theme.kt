package com.antigravity.studio.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Slate950 = Color(0xFF0B0F19)
val Slate900 = Color(0xFF111827)
val Slate800 = Color(0xFF1E293B)
val Slate700 = Color(0xFF334155)
val Slate400 = Color(0xFF94A3B8)
val Slate200 = Color(0xFFE2E8F0)

val Indigo500 = Color(0xFF6366F1)
val Indigo600 = Color(0xFF4F46E5)
val Sky400 = Color(0xFF38BDF8)
val Emerald400 = Color(0xFF34D399)
val Amber400 = Color(0xFFFBBF24)
val Rose400 = Color(0xFFFB7185)

private val AntigravityDarkColorScheme = darkColorScheme(
    primary = Indigo500,
    onPrimary = Color.White,
    primaryContainer = Indigo600,
    onPrimaryContainer = Color.White,
    secondary = Sky400,
    onSecondary = Slate950,
    tertiary = Emerald400,
    background = Slate950,
    onBackground = Slate200,
    surface = Slate900,
    onSurface = Slate200,
    surfaceVariant = Slate800,
    onSurfaceVariant = Slate400,
    error = Rose400
)

@Composable
fun AntigravityStudioTheme(
    content: @Composable () -> Unit
) {
    MaterialTheme(
        colorScheme = AntigravityDarkColorScheme,
        content = content
    )
}
