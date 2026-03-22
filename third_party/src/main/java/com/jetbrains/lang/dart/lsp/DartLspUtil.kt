package com.jetbrains.lang.dart.lsp

import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.util.registry.Registry

object DartLspUtil {
    private val LSP4IJ_PLUGIN_ID = PluginId.getId("com.redhat.devtools.lsp4ij")

    @JvmStatic
    fun isLspMode(): Boolean {
        return Registry.`is`("dart.use.lsp.client", false)
                && PluginManagerCore.getPlugin(LSP4IJ_PLUGIN_ID)?.isEnabled == true
    }
}
